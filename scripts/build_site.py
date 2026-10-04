"""Assemble the static site: web/ + pipeline data -> site/

    WFI_API_URL=https://wfi-api.onrender.com python scripts/build_site.py

WFI_API_URL is optional. When set, the dashboard enables analyst feedback and
the CSP connect-src allows exactly that origin and nothing else.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
WEB, SITE = ROOT / "web", ROOT / "site"


def main() -> int:
    api = os.environ.get("WFI_API_URL", "").strip().rstrip("/")
    origin = ""
    if api:
        u = urlsplit(api)
        if u.scheme != "https" or not u.hostname:
            print("WFI_API_URL must be an https URL", file=sys.stderr)
            return 1
        origin = f"https://{u.netloc}"
    SITE.mkdir(exist_ok=True)
    for item in WEB.iterdir():
        dst = SITE / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    html = (SITE / "index.html").read_text(encoding="utf-8")
    html = html.replace("__API_ORIGIN__", origin).replace("__API_URL__", api)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    (SITE / ".nojekyll").write_text("")
    print(f"site built at {SITE} (api={'on' if api else 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
