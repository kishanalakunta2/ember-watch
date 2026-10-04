"""Evidence API (PRD §9.2 "EVIDENCE API", FR-26, FR-29, FR-30, FR-37).

Serves the pipeline's published data products to authorised users, records
analyst feedback in a tamper-evident audit log, and exposes a structured
query tool that an LLM may call (the LLM translates, this service computes).

Run locally:  uvicorn api.app:app --port 8000
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from wildfire.geo import haversine_km

from .security import AUDIT, READ_LIMIT, WRITE_LIMIT, Principal, rate_limited, require

log = logging.getLogger("wildfire.api")
DATA_DIR = Path(os.environ.get("DATA_DIR", "site/data"))
DATA_URL = os.environ.get("DATA_URL", "").rstrip("/")       # e.g. https://<user>.github.io/<repo>/data
JID = PathParam(pattern=r"^[a-z0-9][a-z0-9_-]{0,40}$")
CID = PathParam(pattern=r"^C-[0-9A-F]{6}$")
FILES = ["summary.json", "config.json", "candidates.json", "risk_cells.json"]

app = FastAPI(title="Wildfire Intelligence Evidence API", version="0.3.0",
              docs_url="/docs" if os.environ.get("ENABLE_DOCS") == "1" else None, redoc_url=None, openapi_url=
              "/openapi.json" if os.environ.get("ENABLE_DOCS") == "1" else None)
origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["X-API-Key", "Content-Type"], max_age=600)

MAX_BODY = 16 * 1024


@app.middleware("http")
async def harden(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers.update({
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Cache-Control": "no-store",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    })
    return resp


# ---------------------------------------------------------------- data store
class Store:
    """Loads data products from disk or from the published site, verifying
    each file's SHA-256 against manifest.json before trusting it."""

    def __init__(self):
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()
        self.ttl = 300

    def _fetch(self, jid: str, name: str) -> bytes:
        if DATA_URL:
            if urlsplit(DATA_URL).scheme != "https":
                raise RuntimeError("DATA_URL must be https")
            r = httpx.get(f"{DATA_URL}/{jid}/{name}", timeout=30, follow_redirects=False)
            r.raise_for_status()
            return r.content
        p = (DATA_DIR / jid / name).resolve()
        if DATA_DIR.resolve() not in p.parents:
            raise FileNotFoundError(name)
        return p.read_bytes()

    def load(self, jid: str) -> dict:
        with self.lock:
            hit = self.cache.get(jid)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        try:
            manifest = json.loads(self._fetch(jid, "manifest.json"))
            bundle = {}
            for name in FILES:
                raw = self._fetch(jid, name)
                if hashlib.sha256(raw).hexdigest() != manifest["files"].get(name):
                    raise RuntimeError(f"integrity check failed for {jid}/{name}")
                bundle[name.split(".")[0]] = json.loads(raw)
        except FileNotFoundError:
            raise HTTPException(404, "Unknown jurisdiction") from None
        except (httpx.HTTPError, RuntimeError, KeyError, json.JSONDecodeError) as e:
            log.error("data load failed for %s: %s", jid, e)
            raise HTTPException(503, "Data products unavailable or failed integrity check") from None
        with self.lock:
            self.cache[jid] = (time.time(), bundle)
        return bundle


STORE = Store()


# ---------------------------------------------------------------- schemas
class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["confirmed", "rejected", "corrected", "verification_requested"]
    note: str = Field(default="", max_length=1000)
    corrected_lat: float | None = Field(default=None, ge=-90, le=90)
    corrected_lon: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("note")
    @classmethod
    def clean(cls, v: str) -> str:
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", v).strip()


class CandidateQuery(BaseModel):
    """The single approved query tool. An LLM may fill it; it never computes."""
    model_config = ConfigDict(extra="forbid")
    routes: list[Literal["priority_review", "verify", "monitor_known", "watch", "likely_static"]] | None = None
    min_score: float = Field(default=0, ge=0, le=1)
    within_km_of_place: float | None = Field(default=None, gt=0, le=200)
    downwind_only: bool = False
    min_fire_weather_class: int | None = Field(default=None, ge=0, le=10)
    seen_within_hours: float | None = Field(default=None, gt=0, le=240)


def run_query(bundle: dict, q: CandidateQuery) -> list[dict]:
    now = datetime.fromisoformat(bundle["summary"]["generated_at"])
    out = []
    for c in bundle["candidates"]:
        if q.routes and c["route"] not in q.routes:
            continue
        if c["score"] < q.min_score:
            continue
        if q.seen_within_hours and datetime.fromisoformat(c["last_seen"]) < now - timedelta(hours=q.seen_within_hours):
            continue
        cells = bundle["risk_cells"]
        if q.min_fire_weather_class is not None:
            w = c.get("weather")
            ci = cells.get(w["cell_id"], {}).get("days", [{}])[0].get("class_index", -1) if w else -1
            if ci < q.min_fire_weather_class:
                continue
        exp = c["exposure"]
        if q.downwind_only:
            exp = [e for e in exp if e["downwind"]]
        if q.within_km_of_place is not None:
            exp = [e for e in exp if e["distance_km"] <= q.within_km_of_place]
            if not exp:
                continue
        elif q.downwind_only and not exp:
            continue
        out.append({"id": c["id"], "route": c["route"], "score": c["score"], "lat": c["lat"], "lon": c["lon"],
                    "last_seen": c["last_seen"], "matching_places": exp})
    return out


# ---------------------------------------------------------------- routes
@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/v1/jurisdictions")
def jurisdictions(p: Principal = Depends(rate_limited(READ_LIMIT))):
    if DATA_URL:
        return httpx.get(f"{DATA_URL}/jurisdictions.json", timeout=30).json()
    return json.loads((DATA_DIR / "jurisdictions.json").read_text())


@app.get("/v1/{jid}/summary")
def summary(jid: str = JID, p: Principal = Depends(rate_limited(READ_LIMIT))):
    return STORE.load(jid)["summary"]


@app.get("/v1/{jid}/candidates")
def candidates(jid: str = JID, route: str | None = Query(default=None, max_length=32),
               p: Principal = Depends(rate_limited(READ_LIMIT))):
    cs = STORE.load(jid)["candidates"]
    return [c for c in cs if not route or c["route"] == route]


@app.get("/v1/{jid}/candidates/{cid}")
def candidate(jid: str = JID, cid: str = CID, p: Principal = Depends(rate_limited(READ_LIMIT))):
    for c in STORE.load(jid)["candidates"]:
        if c["id"] == cid:
            fb = [r for r in AUDIT.read("feedback") if r["data"]["jurisdiction"] == jid and r["data"]["candidate"] == cid]
            return {**c, "feedback": fb}
    raise HTTPException(404, "Unknown candidate")


@app.get("/v1/{jid}/risk")
def risk_at(jid: str = JID, lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
            p: Principal = Depends(rate_limited(READ_LIMIT))):
    cells = STORE.load(jid)["risk_cells"].values()
    best = min(cells, key=lambda c: haversine_km(lat, lon, c["lat"], c["lon"]), default=None)
    if not best:
        raise HTTPException(404, "No risk grid")
    return {**best, "distance_km": round(haversine_km(lat, lon, best["lat"], best["lon"]), 1)}


@app.get("/v1/{jid}/sources")
def sources(jid: str = JID, p: Principal = Depends(rate_limited(READ_LIMIT))):
    s = STORE.load(jid)["summary"]
    return {"sources": s["sources"], "model_card": s["model_card"], "run": s["run"]}


@app.post("/v1/{jid}/query")
def query(q: CandidateQuery, jid: str = JID, p: Principal = Depends(rate_limited(READ_LIMIT))):
    return {"query": q.model_dump(), "results": run_query(STORE.load(jid), q)}


@app.post("/v1/{jid}/candidates/{cid}/feedback", status_code=201)
def feedback(body: Feedback, jid: str = JID, cid: str = CID,
             p: Principal = Depends(require("analyst")), _: Principal = Depends(rate_limited(WRITE_LIMIT))):
    if not any(c["id"] == cid for c in STORE.load(jid)["candidates"]):
        raise HTTPException(404, "Unknown candidate")
    rec = AUDIT.append("feedback", {"jurisdiction": jid, "candidate": cid, "by": p.key_id, "role": p.role,
                                    **body.model_dump()})
    return {"recorded": rec["ts"], "hash": rec["hash"]}


@app.get("/v1/audit")
def audit(limit: int = Query(default=200, ge=1, le=5000), p: Principal = Depends(require("admin"))):
    return {"integrity": AUDIT.verify(), "entries": AUDIT.read(limit=limit)}


class Ask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)


ASK_SYSTEM = ("Translate the analyst's question into arguments for the tool `query_candidates`. "
              "Reply with JSON only, matching this schema: " + json.dumps(CandidateQuery.model_json_schema()) +
              " Route meanings: priority_review=likely new fire, verify=needs camera/UAS check, "
              "monitor_known=matches incident, watch=weak, likely_static=gas flare. "
              "Fire-weather classes are indexed from 0 (Low) upward.")


@app.post("/v1/{jid}/ask")
def ask(body: Ask, jid: str = JID, p: Principal = Depends(require("analyst")),
        _: Principal = Depends(rate_limited(WRITE_LIMIT))):
    base, model = os.environ.get("LLM_BASE_URL", "").rstrip("/"), os.environ.get("LLM_MODEL", "")
    if not base or not model:
        raise HTTPException(503, "Natural-language queries need LLM_BASE_URL and LLM_MODEL; use /query directly")
    headers = {"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"} if os.environ.get("LLM_API_KEY") else {}
    try:
        r = httpx.post(f"{base}/chat/completions", headers=headers, timeout=60, json={
            "model": model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": ASK_SYSTEM}, {"role": "user", "content": body.question}]})
        r.raise_for_status()
        text = re.sub(r"<think>.*?</think>", "", r.json()["choices"][0]["message"]["content"], flags=re.S)
        q = CandidateQuery.model_validate_json(text.strip())
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "Could not translate the question into a valid query; try rephrasing") from None
    results = run_query(STORE.load(jid), q)
    AUDIT.append("ask", {"jurisdiction": jid, "by": p.key_id, "question": body.question, "query": q.model_dump(),
                         "results": len(results)})
    return {"question": body.question, "tool": "query_candidates", "arguments": q.model_dump(),
            "results": results, "note": "Results computed by the evidence service, not by the language model."}
