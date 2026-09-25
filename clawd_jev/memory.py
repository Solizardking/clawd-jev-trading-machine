"""Supermemory recall/write for the JEV loop (fail-soft).

Mirrors the musebook JEV endpoints' memory pattern: recall relevant past
context before the JEV call, write the decision outcome back after the
(simulated) fill so future cycles can recall it.

API contract follows ~/workspace/skills/supermemory (bin/sm.py):
  POST {base}/v4/search     {"q", "containerTag", "containerTags", ...}
  POST {base}/v3/documents  {"content", "containerTag", "taskType", ...}
Auth is a Bearer SUPERMEMORY_API_KEY header. The key is read from the
environment at call time and is never logged, stored, or written to run
reports. Container tag is scoped to this project (clawd-jev-trader) so
memories never cross into other containers.

Fail-soft: never raises. With no key, recall returns enabled=False and
write is a no-op; both label themselves honestly in the JEV state.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

TAG = "clawd-jev-trader"
DEFAULT_BASE_URL = "https://api.supermemory.ai"
USER_AGENT = "clawd-jev/0.2"


def _base_url() -> str:
    return os.environ.get("SUPERMEMORY_BASE_URL", DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL


def _key() -> str | None:
    return os.environ.get("SUPERMEMORY_API_KEY") or None


def enabled() -> bool:
    """Key presence only; the value is never exposed."""
    return bool(_key())


def _post(path: str, body: dict, timeout: float) -> dict:
    key = _key()
    if not key:
        raise RuntimeError("SUPERMEMORY_API_KEY is not set")
    req = urllib.request.Request(
        _base_url() + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "user-agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:300]
        except Exception:
            detail = ""
        raise RuntimeError(f"Supermemory HTTP {e.code}: {detail}")
    except Exception as e:
        raise RuntimeError(f"Supermemory request failed: {type(e).__name__}: {e}")
    if not isinstance(payload, dict):
        raise RuntimeError("Supermemory returned a non-object response")
    return payload


def recall(query: str, *, limit: int = 5, timeout: float = 15.0) -> dict:
    """Recall relevant memories. -> {"enabled", "memories": [str], "error"}.

    Never raises; on any failure returns enabled=True with the error labeled
    and an empty memory list.
    """
    if not _key():
        return {"enabled": False, "memories": [],
                "error": "SUPERMEMORY_API_KEY not set - memory disabled",
                "tag": TAG, "fetched_at": None}
    # Send both singular and plural container-tag fields: /v3 documents only
    # honors the plural array (verified gotcha in the supermemory skill).
    body = {"q": query, "containerTag": TAG, "containerTags": [TAG],
            "searchMode": "memories", "limit": max(1, min(limit, 20))}
    try:
        payload = _post("/v4/search", body, timeout)
    except RuntimeError as e:
        return {"enabled": True, "memories": [], "error": str(e),
                "tag": TAG, "fetched_at": None}
    texts = []
    for m in (payload.get("results") or [])[:limit]:
        if not isinstance(m, dict):
            continue
        txt = m.get("content") or m.get("text") or m.get("summary") or ""
        txt = str(txt).strip()
        if txt:
            texts.append(txt[:600])
    return {"enabled": True, "memories": texts, "error": None,
            "tag": TAG, "fetched_at": time.time()}


def write(summary: str, *, metadata: dict | None = None,
          timeout: float = 30.0) -> dict:
    """Store a decision outcome. -> {"enabled", "ok", "error"}.

    Never raises; never ingests secrets (callers must pass plain summaries).
    """
    if not _key():
        return {"enabled": False, "ok": False,
                "error": "SUPERMEMORY_API_KEY not set - memory disabled"}
    if not (summary or "").strip():
        return {"enabled": True, "ok": False, "error": "empty summary, not stored"}
    meta = {"source": "clawd-jev-trader"}
    if metadata:
        meta.update({k: v for k, v in metadata.items()
                     if isinstance(k, str) and not k.lower().endswith("key")})
    body = {"content": summary.strip()[:2000], "containerTag": TAG,
            "containerTags": [TAG], "taskType": "memory", "metadata": meta}
    try:
        _post("/v3/documents", body, timeout)
    except RuntimeError as e:
        return {"enabled": True, "ok": False, "error": str(e)}
    return {"enabled": True, "ok": True, "error": None}
