"""Run persistence under runtime/<mode>/. Never persists secrets.

The Config object carries no secret values (only presence flags), and run
records contain decisions, quotes, fills, and portfolio snapshots only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def new_run_dir(mode: str, state_root) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    d = Path(state_root) / mode / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_jsonl(path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def write_json(path, obj: dict) -> None:
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    tmp.replace(path)
