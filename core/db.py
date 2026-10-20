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
import time

log = logging.getLogger("tradeos")

_lock = threading.Lock()
_db = None
_tried = False
_last_attempt = 0.0


def _connect():
    global _db, _tried, _last_attempt
    with _lock:
        if _db is not None:
            return _db
        now = time.monotonic()
        if _tried and now - _last_attempt < 30:
            return None
        _tried = True
        _last_attempt = now
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


def status() -> dict:
    """Small, secret-free connectivity status for /healthz."""
    configured = bool(os.getenv("MONGODB_URI", "").strip())
    return {"configured": configured, "connected": available() if configured else False}


def insert_trade(row: dict):
    """Mirror one trade-log row (dict of TradeLog.FIELDS)."""
    db = _connect()
    if db is None:
        return
    try:
        db.trades.insert_one(dict(row))
    except Exception as e:
        log.warning("MongoDB trade insert failed: %s", e)


def save_position_bracket(symbol: str, doc: dict):
    """Persist the stop-loss/take-profit WE intended for an open position,
    keyed by symbol. Exchange-side brackets can be silently partially
    rejected (e.g. Delta cancelling just the stop-loss leg for violating
    margin/liquidation bounds) with no further notice; reconcile() falls
    back to this so the position still gets local (software) protection
    instead of running naked, and so that protection survives a restart."""
    db = _connect()
    if db is None:
        return
    try:
        db.position_brackets.update_one({"_id": symbol}, {"$set": dict(doc)}, upsert=True)
    except Exception as e:
        log.warning("MongoDB bracket save failed: %s", e)


def load_position_bracket(symbol: str) -> dict | None:
    db = _connect()
    if db is None:
        return None
    try:
        doc = db.position_brackets.find_one({"_id": symbol})
        return doc
    except Exception as e:
        log.warning("MongoDB bracket load failed: %s", e)
        return None


def clear_position_bracket(symbol: str):
    db = _connect()
    if db is None:
        return
    try:
        db.position_brackets.delete_one({"_id": symbol})
    except Exception as e:
        log.warning("MongoDB bracket clear failed: %s", e)


def load_trades() -> list[dict] | None:
    """All mirrored trade-log rows, oldest first. None if Mongo is unavailable
    (caller should fall back to the local CSV), [] if Mongo has no rows yet."""
    db = _connect()
    if db is None:
        return None
    try:
        return list(db.trades.find({}, {"_id": 0}).sort("ts", 1))
    except Exception as e:
        log.warning("MongoDB trade load failed: %s", e)
        return None


def load_state() -> dict | None:
    """The current runtime-state document. None if Mongo is unavailable
    (caller should fall back to the local JSON file)."""
    db = _connect()
    if db is None:
        return None
    try:
        doc = db.runtime_state.find_one({"_id": "current"}, {"_id": 0})
        return doc or {}
    except Exception as e:
        log.warning("MongoDB state load failed: %s", e)
        return None


def save_state(doc: dict):
    """Upsert the single current runtime-state document."""
    db = _connect()
    if db is None:
        return
    try:
        db.runtime_state.update_one({"_id": "current"}, {"$set": dict(doc)}, upsert=True)
    except Exception as e:
        log.warning("MongoDB state save failed: %s", e)


def delete_state_fields(*names: str):
    db = _connect()
    if db is None or not names:
        return
    try:
        db.runtime_state.update_one(
            {"_id": "current"}, {"$unset": {name: "" for name in names}}
        )
    except Exception as e:
        log.warning("MongoDB state cleanup failed: %s", e)


def acquire_execution_lease(owner: str, ttl_seconds: int = 30) -> bool:
    """Atomically elect one DELTA_DEMO executor across all instances."""
    db = _connect()
    if db is None:
        return False
    now = time.time()
    try:
        doc = db.execution_lease.find_one_and_update(
            {"_id": "delta-demo", "$or": [
                {"owner": owner}, {"expires_at": {"$lte": now}},
                {"expires_at": {"$exists": False}},
            ]},
            {"$set": {"owner": owner, "expires_at": now + ttl_seconds,
                       "renewed_at": now}},
            upsert=True, return_document=True,
        )
        return bool(doc and doc.get("owner") == owner)
    except Exception as e:
        # A duplicate-key error means another instance owns the singleton.
        if getattr(e, "code", None) == 11000:
            return False
        log.warning("execution lease unavailable: %s", e)
        return False


def release_execution_lease(owner: str):
    db = _connect()
    if db is None:
        return
    try:
        db.execution_lease.delete_one({"_id": "delta-demo", "owner": owner})
    except Exception as e:
        log.warning("execution lease release failed: %s", e)
