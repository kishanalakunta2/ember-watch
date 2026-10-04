"""Print one GitHub Actions notice per jurisdiction summarising source health."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "site/data")
for idx in sorted(root.glob("*/summary.json")):
    s = json.loads(idx.read_text())
    k = s["kpis"]
    src = "; ".join(f"{x['provider']}/{x['dataset'][:24]}={x['status']}({x['records']})" + (f" {x['message'][:80]}" if x.get("message") else "")
                    for x in s["sources"])
    level = "warning" if any(x["status"] in ("failed", "skipped") for x in s["sources"]) else "notice"
    print(f"::{level} title={s['jurisdiction']['id']} ({s['run']['mode']})::"
          f"cells={k['grid_cells']} detections48h={k['detections_48h']} candidates={k['candidates_by_route']} "
          f"incidents={k['known_incidents']} peakFWI={k['peak_fwi_today']} | {src}")
