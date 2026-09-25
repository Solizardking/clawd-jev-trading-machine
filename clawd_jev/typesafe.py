"""Minimal TypeSafe System One client (stdlib only).

Auth: TYPESAFE_API_KEY is read from the environment at call time. The key
value is never logged, never stored, never returned, and never written to
run reports.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class JevError(Exception):
    """Anything that prevents obtaining a usable JEV judgment."""


def ask(state, questions, *, model="jev-latest",
        base_url="https://api.typesafe.ai", timeout_s=30.0) -> dict:
    """POST one System One request. Returns {answers, model, latency_ms}."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("TYPESAFE_API_KEY is not set")
    if not isinstance(questions, dict) or not questions:
        raise JevError("questions must be a non-empty dict")
    body = {
        "state": state if isinstance(state, str) else json.dumps(state),
        "model": model,
        "questions": questions,
    }
    url = base_url.rstrip("/") + "/v1/systemone"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:500]
        except Exception:
            detail = ""
        raise JevError(f"TypeSafe HTTP {e.code}: {detail}")
    except Exception as e:  # timeouts, DNS, TLS, bad JSON
        raise JevError(f"TypeSafe request failed: {type(e).__name__}: {e}")
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevError("TypeSafe response had no answers object")
    return {
        "answers": answers,
        "model": payload.get("model"),
        "latency_ms": int((time.time() - started) * 1000),
    }
