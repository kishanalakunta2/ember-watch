"""Create an API key:  python -m api.keys analyst

Prints the key once (give it to the user) and the JSON entry to add to the
API_KEYS secret. The server never stores the key itself, only its hash.
"""
import json
import sys

from .security import ROLES, new_key

if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else "viewer"
    if role not in ROLES:
        sys.exit(f"role must be one of {list(ROLES)}")
    key, kid, entry = new_key(role)
    print("API key (shown once):", key)
    print("Add to API_KEYS:", json.dumps({kid: entry}))
