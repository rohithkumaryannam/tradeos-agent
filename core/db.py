"""Optional MongoDB persistence. Set MONGODB_URI in .env to enable; every
closed trade and the latest runtime snapshot are mirrored into Atlas so the
data survives host restarts/redeploys. No-op (never raises) if unset or
unreachable — Mongo is a durable mirror, not a dependency the agent needs to
run.
"""
from __future__ import annotations
import logging
import os
import threading

log = logging.getLogger("tradeos")

_lock = threading.Lock()
_db = None
_tried = False


def _connect():
    global _db, _tried
    with _lock:
        if _tried:
            return _db
        _tried = True
        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri:
            return None
        try:
            from pymongo import MongoClient
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            db = client.get_default_database()
            if db is None:
                db = client["tradeos"]
            db.command("ping")
            db.trades.create_index("ts")
            db.trades.create_index("strategy")
            _db = db
            log.info("MongoDB connected: db=%s", db.name)
        except Exception as e:
            log.warning("MongoDB unavailable, continuing without it: %s", e)
            _db = None
        return _db


def available() -> bool:
    return _connect() is not None


def insert_trade(row: dict):
    """Mirror one trade-log row (dict of TradeLog.FIELDS)."""
    db = _connect()
    if db is None:
        return
    try:
        db.trades.insert_one(dict(row))
    except Exception as e:
        log.warning("MongoDB trade insert failed: %s", e)


def save_state(doc: dict):
    """Upsert the single current runtime-state document."""
    db = _connect()
    if db is None:
        return
    try:
        db.runtime_state.update_one({"_id": "current"}, {"$set": dict(doc)}, upsert=True)
    except Exception as e:
        log.warning("MongoDB state save failed: %s", e)
