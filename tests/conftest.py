"""Tests never use developer credentials, network access, or runtime state."""
import pytest
import requests
from core.delta_client import DeltaClient
from core import ai, db, state


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(DeltaClient, "get_product", lambda self, symbol: {
        "id": {"BTCUSD": 1, "ETHUSD": 2, "SOLUSD": 3}.get(symbol, 4),
        "contract_value": 0.001,
    })
    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in tests")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(ai, "available", lambda: False)
    monkeypatch.setattr(state, "PATH", tmp_path / "runtime_state.json")
    # A real MONGODB_URI in .env must never make tests hit the live cluster.
    monkeypatch.setattr(db, "_connect", lambda: None)
