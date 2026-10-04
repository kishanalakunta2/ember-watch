"""Optional LLM intelligence layer (PRD §7.7, FR-26, FR-28).

The LLM never sees pixels and never computes numbers. It receives the
structured evidence produced by the analytics, may only restate it, and its
output is machine-checked: any sentence containing a number that does not
appear in the evidence is removed and counted as an unsupported claim.

Works with any OpenAI-compatible endpoint:
  * local Qwen3-8B via Ollama or vLLM     (PRD baseline, data stays on-prem)
  * GitHub Models in Actions               (free; PROPRIETARY / EXTERNAL PROCESSING — benchmark only)
If LLM_BASE_URL is unset the platform uses the deterministic brief, so the
core never depends on an LLM (NFR 5.5).
"""
from __future__ import annotations

import json
import logging
import os
import re
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("wildfire.llm")
NUM = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)?")

SYSTEM = """You are an evidence-bound wildfire intelligence assistant for government analysts.
Rules:
- Use ONLY facts present in the EVIDENCE JSON. Do not use outside knowledge.
- Copy numbers exactly as they appear in the evidence. Never compute, round or estimate new numbers.
- Every statement must cite evidence ids from the input (candidate ids, cell ids or source names).
- Sort statements into: observed (sensor/agency reports), calculated (deterministic analytics),
  model_derived (index or model outputs), unknown (what the evidence cannot tell).
- Never recommend evacuation, dispatch or public warnings. Humans make those decisions.
Return JSON: {"headline": str, "observed": [{"text": str, "evidence": [str]}], "calculated": [...],
"model_derived": [...], "unknown": [...]}"""


def configured() -> bool:
    return bool(os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_MODEL"))


def _numbers(s: str) -> set[str]:
    return {n.replace(",", ".").rstrip("0").rstrip(".") for n in NUM.findall(s)}


def verify(sections: dict, evidence_text: str) -> tuple[dict, int]:
    allowed = _numbers(evidence_text)
    removed = 0
    clean = {}
    for key in ("observed", "calculated", "model_derived", "unknown"):
        keep = []
        for st in sections.get(key, []) or []:
            text = str(st.get("text", ""))[:600]
            if not _numbers(text) <= allowed:
                removed += 1
                continue
            keep.append({"text": text, "evidence": [str(e)[:60] for e in st.get("evidence", [])][:12]})
        clean[key] = keep
    return clean, removed


def brief(evidence: dict) -> dict | None:
    if not configured():
        return None
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://localhost") and not base.startswith("http://127.0.0.1"):
        log.error("LLM_BASE_URL must be https (or localhost)")
        return None
    model = os.environ["LLM_MODEL"]
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("LLM_API_KEY")
    if not token and urlsplit(base).hostname == "models.github.ai":
        token = os.environ.get("GITHUB_MODELS_TOKEN")   # the Actions token never goes to any other host
    if token:
        headers["Authorization"] = f"Bearer {token}"
    ev_text = json.dumps(evidence, separators=(",", ":"), default=str)[:60000]
    body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "EVIDENCE:\n" + ev_text}]}
    try:
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()   # Qwen3 thinking traces
        raw = json.loads(content)
    except Exception as e:  # noqa: BLE001 — any LLM failure degrades to the deterministic brief
        log.warning("LLM brief failed (%s); using deterministic brief", type(e).__name__)
        return None
    sections, removed = verify(raw, ev_text)
    headline = str(raw.get("headline", ""))[:240]
    if not _numbers(headline) <= _numbers(ev_text):
        headline, removed = "", removed + 1
    external = not any(h in base for h in ("localhost", "127.0.0.1"))
    return {"generated_by": model, "endpoint_class": "EXTERNAL DATA PROCESSING" if external else "local",
            "headline": headline, **sections, "unsupported_claims_removed": removed,
            "status": "draft — requires human approval before release"}
