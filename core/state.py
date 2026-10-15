"""Tiny JSON state file for the bot's DESIRED run-state.

So an always-on host can resume exactly what you last set — running/stopped,
mode, auto, strategy — after a reboot, crash-restart or redeploy. "Start" then
means started until YOU stop it, independent of the host restarting.
"""
from __future__ import annotations
import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "runtime_state.json"


def load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {}


def save(**fields):
    cur = load()
    cur.update(fields)
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(json.dumps(cur))
    except Exception:
        pass
