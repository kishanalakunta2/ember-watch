"""Copy replay outputs into the site and add them to jurisdictions.json.

A replay with no satellite observations (e.g. missing FIRMS key) is not
merged, and its folder is removed so the empty result is not cached.
"""
import json
import shutil
import sys
from pathlib import Path


def main(src: Path, dst: Path) -> int:
    idx = src / "jurisdictions.json"
    if not idx.is_file():
        print("no replay output")
        return 0
    entries = json.loads(idx.read_text())
    good = []
    for e in entries:
        summary = json.loads((src / e["id"] / "summary.json").read_text())
        if summary["kpis"]["detections_48h"] == 0 and not summary["kpis"]["grid_cells"]:
            continue
        shutil.copytree(src / e["id"], dst / e["id"], dirs_exist_ok=True)
        good.append(e)
    if not good:
        shutil.rmtree(src, ignore_errors=True)   # do not cache an empty replay
        print("replay produced no data; not merged")
        return 0
    site_idx = dst / "jurisdictions.json"
    cur = json.loads(site_idx.read_text()) if site_idx.is_file() else []
    ids = {e["id"] for e in good}
    site_idx.write_text(json.dumps([e for e in cur if e["id"] not in ids] + good))
    print(f"merged {len(good)} replay view(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
