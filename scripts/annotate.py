"""Turn the tail of a log file into a GitHub Actions error annotation.

Annotations are visible in the Checks UI and through the API, which makes
CI failures diagnosable without downloading raw logs.
"""
import sys
from pathlib import Path

path, title = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Step failed"
lines = Path(path).read_text(errors="replace").splitlines()[-60:]
body = "\n".join(lines)
for a, b in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
    body = body.replace(a, b)
print(f"::error title={title}::{body}")
