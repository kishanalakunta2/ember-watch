import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    subprocess.run([sys.executable, "-m", "wildfire.pipeline", "-j", "texas", "--fixtures", "--out", str(data)],
                   cwd=ROOT, check=True, capture_output=True)
    from api import security
    keys, raw = {}, {}
    for role in ("viewer", "analyst", "admin"):
        k, kid, entry = security.new_key(role)
        keys[kid], raw[role] = entry, k
    os.environ.update({"API_KEYS": json.dumps(keys), "DATA_DIR": str(data),
                       "AUDIT_PATH": str(tmp_path_factory.mktemp("var") / "audit.jsonl"),
                       "RATE_WRITE_PER_MIN": "5", "PUBLIC_READ": "0"})
    os.environ.pop("DATA_URL", None)
    importlib.reload(security)
    from api import app as app_mod
    importlib.reload(app_mod)
    c = TestClient(app_mod.app)
    c.keys, c.data = raw, data
    return c


def h(c, role):
    return {"X-API-Key": c.keys[role]}


def test_requires_auth(client):
    assert client.get("/v1/texas/summary").status_code == 401
    assert client.get("/v1/texas/summary", headers={"X-API-Key": "wfi_dead_beef"}).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_viewer_reads_and_headers(client):
    r = client.get("/v1/texas/candidates", headers=h(client, "viewer"))
    assert r.status_code == 200 and r.json()
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'none'" in r.headers["content-security-policy"]


def test_viewer_cannot_write(client):
    cid = client.get("/v1/texas/candidates", headers=h(client, "viewer")).json()[0]["id"]
    r = client.post(f"/v1/texas/candidates/{cid}/feedback", json={"decision": "confirmed"}, headers=h(client, "viewer"))
    assert r.status_code == 403


def test_analyst_feedback_and_audit_chain(client):
    cid = client.get("/v1/texas/candidates", headers=h(client, "viewer")).json()[0]["id"]
    r = client.post(f"/v1/texas/candidates/{cid}/feedback",
                    json={"decision": "verification_requested", "note": "UAS team 3\x07 tasked"}, headers=h(client, "analyst"))
    assert r.status_code == 201
    a = client.get("/v1/audit", headers=h(client, "admin")).json()
    assert a["integrity"]["intact"] is True
    fb = [e for e in a["entries"] if e["event"] == "feedback"][-1]
    assert fb["data"]["note"] == "UAS team 3 tasked"           # control chars stripped
    assert client.get("/v1/audit", headers=h(client, "analyst")).status_code == 403


def test_rejects_bad_input(client):
    hdr = h(client, "analyst")
    assert client.post("/v1/texas/candidates/C-000000/feedback", json={"decision": "nuke"}, headers=hdr).status_code == 422
    assert client.post("/v1/texas/candidates/x;drop/feedback", json={"decision": "confirmed"}, headers=hdr).status_code in (404, 422)
    assert client.get("/v1/..%2Fsecrets/summary", headers=hdr).status_code in (404, 422)
    big = {"decision": "confirmed", "note": "x" * 20000}
    assert client.post("/v1/texas/candidates/C-000000/feedback", json=big, headers=hdr).status_code == 413


def test_integrity_check_blocks_tampered_data(client):
    p = client.data / "texas" / "candidates.json"
    orig = p.read_text()
    from api import app as app_mod
    app_mod.STORE.cache.clear()
    p.write_text(orig.replace('"priority_review"', '"watch"', 1))
    try:
        assert client.get("/v1/texas/candidates", headers=h(client, "viewer")).status_code == 503
    finally:
        p.write_text(orig)
        app_mod.STORE.cache.clear()


def test_structured_query_tool(client):
    r = client.post("/v1/texas/query", json={"routes": ["priority_review", "verify"], "min_score": 0.5},
                    headers=h(client, "viewer"))
    assert r.status_code == 200
    assert all(x["route"] in ("priority_review", "verify") for x in r.json()["results"])


def test_rate_limit_on_writes(client):
    cid = client.get("/v1/texas/candidates", headers=h(client, "viewer")).json()[0]["id"]
    codes = [client.post(f"/v1/texas/candidates/{cid}/feedback", json={"decision": "confirmed"},
                         headers=h(client, "analyst")).status_code for _ in range(8)]
    assert 429 in codes
