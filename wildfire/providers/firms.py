"""NASA FIRMS active-fire adapter (FR-9).

API: https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{W,S,E,N}/{DAYS}
A free MAP_KEY is required; it is read from the FIRMS_MAP_KEY secret and is
never written to logs, provenance or published files.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import http
from ..models import Observation, SourceMeta

log = logging.getLogger("wildfire.firms")
BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
LICENSE = "NASA open data (no restrictions; attribution requested)"
ATTRIB = "NASA FIRMS (LANCE). doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002 et al."

# dataset -> (sensor, default platform, native resolution m)
DATASETS = {
    "VIIRS_SNPP_NRT": ("VIIRS", "Suomi NPP", 375.0),
    "VIIRS_NOAA20_NRT": ("VIIRS", "NOAA-20", 375.0),
    "VIIRS_NOAA21_NRT": ("VIIRS", "NOAA-21", 375.0),
    "MODIS_NRT": ("MODIS", "Terra/Aqua", 1000.0),
}
PLATFORM_CODES = {"N": "Suomi NPP", "N20": "NOAA-20", "N21": "NOAA-21", "1": "NOAA-20",
                  "T": "Terra", "A": "Aqua", "Terra": "Terra", "Aqua": "Aqua"}
# VIIRS confidence classes (Schroeder et al. 2014): low pixels include sun glint and
# weak anomalies; high are saturated/strong. Mapped to a prior probability of fire.
VIIRS_CONF = {"l": 0.30, "low": 0.30, "n": 0.70, "nominal": 0.70, "h": 0.90, "high": 0.90}


def _conf(sensor: str, raw: str) -> float:
    raw = raw.strip().lower()
    if sensor == "VIIRS":
        return VIIRS_CONF.get(raw, 0.5)
    try:  # MODIS 0-100 (Giglio et al. 2016): <30 low, 30-80 nominal, >=80 high
        return max(0.05, min(0.95, float(raw) / 100.0))
    except ValueError:
        return 0.5


def parse_csv(text: str, dataset: str, jurisdiction: str, ingestion: datetime) -> list[Observation]:
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    if not text.startswith("latitude"):
        raise http.FetchError(f"FIRMS {dataset}: unexpected response: {http.redact(text[:120])!r}")
    sensor, default_platform, res = DATASETS[dataset]
    out: list[Observation] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            lat, lon = float(row["latitude"]), float(row["longitude"])
            t = row["acq_time"].strip().zfill(4)
            obs_time = datetime.strptime(f"{row['acq_date']} {t}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
            frp = float(row["frp"]) if row.get("frp") not in (None, "") else None
            platform = PLATFORM_CODES.get(row.get("satellite", "").strip(), default_platform)
            bright = row.get("bright_ti4") or row.get("brightness")
            out.append(Observation(
                id=Observation.make_id(dataset, lat, lon, obs_time.isoformat()),
                provider="nasa_firms", dataset=dataset, sensor=sensor, platform=platform,
                observation_time=obs_time, ingestion_time=ingestion,
                lat=lat, lon=lon, native_resolution_m=res,
                confidence=_conf(sensor, row.get("confidence", "")),
                confidence_raw=row.get("confidence", "").strip(),
                frp_mw=frp, daynight=(row.get("daynight") or "").strip() or None,
                quality={
                    "brightness_k": float(bright) if bright else None,
                    "scan": float(row.get("scan") or 0) or None,
                    "track": float(row.get("track") or 0) or None,
                    "version": row.get("version", ""),
                },
                license=LICENSE, attribution=ATTRIB, jurisdiction=jurisdiction,
            ))
        except (KeyError, ValueError) as e:  # one bad row never sinks the batch
            log.warning("FIRMS %s: skipped malformed row (%s)", dataset, e)
    return out


ARCHIVE = {  # standard-processing (science quality) equivalents for replays
    "VIIRS_SNPP_NRT": "VIIRS_SNPP_SP", "VIIRS_NOAA20_NRT": "VIIRS_NOAA20_SP",
    "MODIS_NRT": "MODIS_SP",
}   # NOAA-21 has no standard-processing archive in FIRMS yet ("Invalid source")


def fetch(pack, ref, *, now: datetime, fixtures: Path | None = None,
          as_of: datetime | None = None) -> tuple[list[Observation], list[SourceMeta]]:
    w, s, e, n = pack.bbox
    area = f"{w:.3f},{s:.3f},{e:.3f},{n:.3f}"
    key = None if fixtures else http.env_secret("FIRMS_MAP_KEY")
    obs: list[Observation] = []
    metas: list[SourceMeta] = []
    for ds in ref.sources:
        if ds not in DATASETS:
            log.warning("unknown FIRMS dataset %s skipped", ds)
            continue
        if as_of and ds not in ARCHIVE:
            metas.append(SourceMeta(provider="nasa_firms", dataset=ds, status="skipped", fetched_at=now,
                                    native_resolution=f"{int(DATASETS[ds][2])} m", expected_refresh="-",
                                    license=LICENSE, attribution=ATTRIB, source_uri="-",
                                    message="no archived (SP) product for replays"))
            continue
        req = ARCHIVE.get(ds, ds) if as_of else ds
        date_part = f"/{(as_of - timedelta(days=ref.day_range - 1)):%Y-%m-%d}" if as_of else ""
        public_uri = f"{BASE}/<MAP_KEY>/{req}/{area}/{ref.day_range}{date_part}"
        meta = dict(provider="nasa_firms", dataset=req if as_of else ds, fetched_at=now,
                    native_resolution=f"{int(DATASETS[ds][2])} m",
                    expected_refresh="Each satellite overpass; NRT latency ~3 h (US/Canada URT faster)",
                    license=LICENSE, attribution=ATTRIB, source_uri=public_uri)
        try:
            if fixtures:
                text = (fixtures / f"firms_{ds}.csv").read_text(encoding="utf-8")
                status = "fixture"
            elif not key:
                metas.append(SourceMeta(status="skipped", message="FIRMS_MAP_KEY secret not set", **meta))
                continue
            else:
                text = http.get(f"{BASE}/{key}/{req}/{area}/{ref.day_range}{date_part}", timeout=90).text
                status = "ok"
            rows = parse_csv(text, ds, pack.id, now)
            obs.extend(rows)
            latest = max((o.observation_time for o in rows), default=None)
            metas.append(SourceMeta(status=status, records=len(rows), latest_observation=latest, **meta))
        except (http.FetchError, OSError) as e:
            log.error("FIRMS %s failed: %s", ds, http.redact(str(e)))
            metas.append(SourceMeta(status="failed", message=http.redact(str(e))[:300], **meta))
    return obs, metas
