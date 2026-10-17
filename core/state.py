"""Tiny JSON state file for the bot's DESIRED run-state.

So an always-on host can resume exactly what you last set — running/stopped,
mode, auto, strategy — after a reboot, crash-restart or redeploy. "Start" then
means started until YOU stop it, independent of the host restarting.
"""
from __future__ import annotations
import json
import os
import tempfile
import threading
from pathlib import Path

from core import db

PATH = Path(__file__).resolve().parent.parent / "data" / "runtime_state.json"
_lock = threading.RLock()


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {}


def save(**fields):
    with _lock:
        cur = load()
        cur.update(fields)
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=PATH.parent, delete=False) as f:
                tmp = f.name
                json.dump(cur, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, PATH)
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
        db.save_state(cur)
