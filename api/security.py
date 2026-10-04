"""Authentication, RBAC, rate limiting and a tamper-evident audit log.

API keys look like  wfi_<keyid>_<secret>. Only SHA-256(secret) is stored
(env API_KEYS, a JSON map), compared in constant time. Roles are ordered
viewer < analyst < admin (least privilege, PRD NFR 5.6).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

ROLES = {"viewer": 1, "analyst": 2, "admin": 3}
_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Principal:
    key_id: str
    role: str


def _load_keys() -> dict[str, dict]:
    raw = os.environ.get("API_KEYS", "").strip()
    if not raw:
        return {}
    keys = json.loads(raw)
    for kid, v in keys.items():
        if v.get("role") not in ROLES or len(v.get("hash", "")) != 64:
            raise RuntimeError(f"API_KEYS entry {kid!r} is invalid")
    return keys


KEYS = _load_keys()
PUBLIC_READ = os.environ.get("PUBLIC_READ", "0") == "1"   # allow anonymous viewer access


def new_key(role: str) -> tuple[str, str, dict]:
    kid = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    return f"wfi_{kid}_{secret}", kid, {"hash": hashlib.sha256(secret.encode()).hexdigest(), "role": role}


def authenticate(request: Request, api_key: str | None = Depends(_header)) -> Principal:
    if not api_key:
        if PUBLIC_READ:
            return Principal("anonymous", "viewer")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    try:
        prefix, kid, secret = api_key.split("_", 2)
    except ValueError:
        prefix, kid, secret = "", "", ""
    entry = KEYS.get(kid) if prefix == "wfi" else None
    digest = hashlib.sha256(secret.encode()).hexdigest()
    expected = entry["hash"] if entry else "0" * 64
    if not hmac.compare_digest(digest, expected) or not entry:
        AUDIT.append("auth_failed", {"key_id": kid[:16], "ip": _client_ip(request)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
    return Principal(kid, entry["role"])


def require(role: str):
    def dep(p: Principal = Depends(authenticate)) -> Principal:
        if ROLES[p.role] < ROLES[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {role} role")
        return p
    return dep


def _client_ip(request: Request) -> str:
    # Only trust X-Forwarded-For when explicitly running behind a known proxy.
    if os.environ.get("TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Token bucket per principal (or IP for anonymous)."""

    def __init__(self, per_minute: int):
        self.rate = per_minute / 60.0
        self.cap = float(per_minute)
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(key, (self.cap, now))
            tokens = min(self.cap, tokens + (now - last) * self.rate)
            if tokens < 1:
                self.buckets[key] = (tokens, now)
                return False
            self.buckets[key] = (tokens - 1, now)
            if len(self.buckets) > 50_000:      # bound memory
                self.buckets.clear()
            return True


READ_LIMIT = RateLimiter(int(os.environ.get("RATE_READ_PER_MIN", "120")))
WRITE_LIMIT = RateLimiter(int(os.environ.get("RATE_WRITE_PER_MIN", "20")))


def rate_limited(limiter: RateLimiter):
    def dep(request: Request, p: Principal = Depends(authenticate)) -> Principal:
        key = p.key_id if p.key_id != "anonymous" else "ip:" + _client_ip(request)
        if not limiter.hit(key):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded", headers={"Retry-After": "30"})
        return p
    return dep


class AuditLog:
    """Append-only JSONL where each entry hashes the previous one.

    Editing or deleting any line breaks the chain, which `verify()` detects.
    """

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return "0" * 64
        with self.path.open("rb") as f:
            f.seek(max(0, self.path.stat().st_size - 4096))
            last = f.read().splitlines()[-1]
        return json.loads(last)["hash"]

    def append(self, event: str, data: dict) -> dict:
        with self.lock:
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, "data": data,
                   "prev": self._last_hash()}
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            return rec

    def read(self, event: str | None = None, limit: int = 500) -> list[dict]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if event:
            rows = [r for r in rows if r["event"] == event]
        return rows[-limit:]

    def verify(self) -> dict:
        prev, n = "0" * 64, 0
        for rec in self.read(limit=10**9):
            body = {k: rec[k] for k in ("ts", "event", "data", "prev")}
            h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if rec["prev"] != prev or rec["hash"] != h:
                return {"intact": False, "broken_at": n}
            prev, n = h, n + 1
        return {"intact": True, "entries": n}


AUDIT = AuditLog(Path(os.environ.get("AUDIT_PATH", "var/audit.jsonl")))
