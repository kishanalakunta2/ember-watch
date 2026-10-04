"""GeoNames populated places (CC BY 4.0) for exposure analysis (FR-16).

Natural Earth (shipped in each pack) only lists larger cities. When the
network allows, we refresh a finer layer from GeoNames `cities1000`
(every place with population >= 1000), clipped to the jurisdiction, and
cache it for a week. If the download fails the pack's layer is used.
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .. import http
from ..geo import point_in_ring
from ..models import SourceMeta

log = logging.getLogger("wildfire.geonames")
URL = "https://download.geonames.org/export/dump/cities1000.zip"
TTL_S = 7 * 86400
http.ALLOWED_HOSTS.add("download.geonames.org")


def parse(txt: str, ring, min_pop: int = 1000) -> dict:
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    feats = []
    for line in txt.splitlines():
        c = line.split("\t")
        if len(c) < 15 or c[6] != "P":
            continue
        try:
            lat, lon, pop = float(c[4]), float(c[5]), int(c[14] or 0)
        except ValueError:
            continue
        if pop < min_pop or not (x0 <= lon <= x1 and y0 <= lat <= y1) or not point_in_ring(lon, lat, ring):
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                      "properties": {"name": c[1], "population": pop, "admin1": c[10]}})
    feats.sort(key=lambda f: -f["properties"]["population"])
    return {"type": "FeatureCollection", "features": feats}


def load(pack, cache_dir: Path | None, now: datetime) -> tuple[dict | None, SourceMeta]:
    meta = dict(provider="geonames", dataset="cities1000 populated places", fetched_at=now,
                native_resolution="Point per place (pop ≥ 1000)", expected_refresh="Weekly",
                license="CC BY 4.0", attribution="GeoNames (geonames.org)", source_uri=URL)
    cache = cache_dir / "cities1000.txt" if cache_dir else None
    try:
        if cache and cache.is_file() and time.time() - cache.stat().st_mtime < TTL_S:
            txt = cache.read_text(encoding="utf-8")
        else:
            raw = http.get(URL, timeout=120).content
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                txt = z.read("cities1000.txt").decode("utf-8")
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(txt, encoding="utf-8")
        fc = parse(txt, pack.boundary_ring)
        return fc, SourceMeta(status="ok", records=len(fc["features"]), **meta)
    except (http.FetchError, OSError, zipfile.BadZipFile, KeyError) as e:
        log.warning("GeoNames unavailable (%s); using the pack's Natural Earth places", e)
        return None, SourceMeta(status="skipped", message="fell back to Natural Earth places", **meta)


def save_json(fc: dict, path: Path) -> None:
    path.write_text(json.dumps(fc, separators=(",", ":")))
