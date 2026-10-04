"""Build a single self-contained presentation page from web/ + site/data.

Fonts become data URIs, CSS and app.js are inlined, MapLibre's UMD build is
loaded from unpkg (pinned), and the pipeline output is embedded as JSON.
Usage:  python scripts/build_artifact.py out.html
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, DATA = ROOT / "web", ROOT / "site" / "data"
MAPLIBRE = "5.24.0"
FILES = ["summary.json", "config.json", "risk.geojson", "risk_cells.json", "detections.geojson",
         "candidates.json", "incidents.geojson", "boundary.geojson", "places.geojson"]


def main(out: str) -> None:
    fonts = (WEB / "fonts.css").read_text()
    fonts = re.sub(r"url\(fonts/([^)]+)\)", lambda m: "url(data:font/woff2;base64," +
                   base64.b64encode((WEB / "fonts" / m.group(1)).read_bytes()).decode() + ")", fonts)
    css = fonts + (WEB / "vendor" / "maplibre-gl.css").read_text() + (WEB / "style.css").read_text()
    js = (WEB / "app.js").read_text()
    juris = json.loads((DATA / "jurisdictions.json").read_text())
    packs = {j["id"]: {f.split(".")[0]: json.loads((DATA / j["id"] / f).read_text()) for f in FILES} for j in juris}
    data = json.dumps({"jurisdictions": juris, "packs": packs}, separators=(",", ":")).replace("</", "<\\/")
    body = re.search(r"<!--BODY-->(.*)<!--/BODY-->", (WEB / "index.html").read_text(), re.S).group(1)
    note = ('<p class="brief" style="font-size:13px"><span class="lbl">About this copy</span><span>This presentation copy '
            'embeds one sample run of the real pipeline on synthetic inputs. The deployed site on GitHub Pages '
            'refreshes from live NASA FIRMS, NOAA HRRR and NIFC WFIGS feeds every 30 minutes.</span></p>')
    body = body.replace('<p id="fatal"', note + '\n  <p id="fatal"', 1)
    body = body.replace("Map tiles © OpenStreetMap contributors © CARTO.", "")
    page = f"""<title>Ember Watch</title>
<meta name="wfi-api" content="">
<style>{css}</style>
<script src="https://unpkg.com/maplibre-gl@{MAPLIBRE}/dist/maplibre-gl.js"></script>
<script type="application/json" id="embedded-data">{data}</script>
{body}
<script>{js}</script>
"""
    Path(out).write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ember-watch-demo.html")
