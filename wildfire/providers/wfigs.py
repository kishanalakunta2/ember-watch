"""NIFC WFIGS incident adapter (FR-12, US enhancement pack).

Uses a bounding-box spatial query, so the adapter itself is not tied to any
state. Field names are read defensively because ArcGIS schemas change.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
import json

from .. import http
from ..geo import point_in_ring
from ..models import Incident, SourceMeta

log = logging.getLogger("wildfire.wfigs")
SVC = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
INCIDENTS = f"{SVC}/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
PERIMETERS = f"{SVC}/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
LICENSE = "US Government public data (NIFC Open Data)"
ATTRIB = "National Interagency Fire Center — WFIGS"


def _pick(p: dict, *names):
    for n in names:
        if n in p and p[n] not in (None, ""):
            return p[n]
    lower = {k.lower(): v for k, v in p.items()}
    for n in names:
        v = lower.get(n.lower())
        if v not in (None, ""):
            return v
    return None


def _ts(v) -> datetime | None:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OSError):
        return None


def _query(url: str, bbox, extra: dict) -> dict:
    w, s, e, n = bbox
    feats: list[dict] = []
    offset = 0
    while True:
        params = {"where": "1=1", "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                  "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
                  "outSR": 4326, "f": "geojson", "resultOffset": offset, "resultRecordCount": 1000, **extra}
        page = http.get(url, params=params, timeout=90).json()
        if "error" in page:
            raise http.FetchError(f"ArcGIS error: {str(page['error'])[:200]}")
        got = page.get("features", [])
        feats.extend(got)
        exceeded = page.get("exceededTransferLimit") or page.get("properties", {}).get("exceededTransferLimit")
        if not exceeded or not got or len(feats) > 20000:
            break
        offset += len(got)
    return {"type": "FeatureCollection", "features": feats}


def parse(inc_fc: dict, per_fc: dict, ring) -> list[Incident]:
    perims: dict[str, dict] = {}
    for f in per_fc.get("features", []):
        p = f.get("properties", {})
        k = _pick(p, "attr_UniqueFireIdentifier", "poly_UniqueFireIdentifier", "attr_IrwinID", "poly_IRWINID")
        if k and f.get("geometry"):
            perims[str(k).strip("{}").lower()] = f["geometry"]
    out = []
    for f in inc_fc.get("features", []):
        g, p = f.get("geometry") or {}, f.get("properties", {})
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        if not point_in_ring(lon, lat, ring):
            continue
        kind = str(_pick(p, "IncidentTypeCategory") or "")
        if kind.upper() == "RX":      # prescribed burns are not wildfires
            continue
        uid = str(_pick(p, "UniqueFireIdentifier", "IrwinID", "OBJECTID") or "")
        irwin = str(_pick(p, "IrwinID") or "").strip("{}").lower()
        out.append(Incident(
            id=uid or f"wfigs-{lat:.3f},{lon:.3f}",
            name=str(_pick(p, "IncidentName") or "Unnamed incident").title(),
            lat=lat, lon=lon,
            size_acres=_num(_pick(p, "IncidentSize", "DailyAcres", "CalculatedAcres", "DiscoveryAcres")),
            percent_contained=_num(_pick(p, "PercentContained")),
            discovered=_ts(_pick(p, "FireDiscoveryDateTime")),
            updated=_ts(_pick(p, "ModifiedOnDateTime_dt", "ModifiedOnDateTime")),
            kind=kind, provider="nifc_wfigs",
            perimeter=perims.get(uid.lower()) or perims.get(irwin),
        ))
    return out


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch(pack, ref, *, now: datetime, fixtures: Path | None = None):
    meta = dict(provider="nifc_wfigs", dataset="WFIGS current incidents + perimeters", fetched_at=now,
                native_resolution="Point of origin; mapped perimeters", expected_refresh="~5 min (agency-reported)",
                license=LICENSE, attribution=ATTRIB, source_uri=INCIDENTS)
    try:
        if fixtures:
            inc = json.loads((fixtures / "wfigs_incidents.geojson").read_text(encoding="utf-8"))
            per = json.loads((fixtures / "wfigs_perimeters.geojson").read_text(encoding="utf-8"))
            status = "fixture"
        else:
            inc = _query(INCIDENTS, pack.bbox, {})
            per = _query(PERIMETERS, pack.bbox, {"maxAllowableOffset": 0.0005, "geometryPrecision": 5})
            status = "ok"
        items = parse(inc, per, pack.boundary_ring)
        latest = max((i.updated or i.discovered for i in items if (i.updated or i.discovered)), default=None)
        return items, [SourceMeta(status=status, records=len(items), latest_observation=latest, **meta)]
    except (http.FetchError, OSError, ValueError) as e:
        log.error("WFIGS failed: %s", e)
        return [], [SourceMeta(status="failed", message=str(e)[:300], **meta)]
