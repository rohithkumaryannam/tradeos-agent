"""Change history for every control-plane action (dashboard or Jarvis).

Each entry records who changed what, when, and the value before and after,
so a setting changed from a phone at 3am can be found and undone. Written to
logs/audit.jsonl and mirrored to MongoDB (collection `audit`) when configured,
so the history survives redeploys on hosts with an ephemeral disk.
"""
from __future__ import annotations
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from core import db

PATH = Path(__file__).resolve().parent.parent / "logs" / "audit.jsonl"
_lock = threading.Lock()


def record(action: str, *, target: str = "", before=None, after=None,
           actor: str = "dashboard", detail: str = "") -> dict:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action, "target": target, "actor": actor,
        "detail": (detail or "")[:500], "before": before, "after": after,
    }
    try:
        with _lock:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PATH, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass
    db.insert_audit(json.loads(json.dumps(entry, default=str)))
    return entry


def recent(limit: int = 200) -> list[dict]:
    """Newest first. Prefers Mongo (complete across redeploys)."""
    limit = max(1, min(int(limit), 2000))
    rows = db.load_audit(limit)
    if rows is not None:
        return rows
    try:
        with open(PATH) as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
