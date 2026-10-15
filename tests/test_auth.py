"""Access-control tests for the dashboard server. When TRADEOS_TOKEN is unset,
the app must behave exactly as before (no auth). When set, every page and API
call must require a valid session cookie, obtained only via /login."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient


def _load_app(monkeypatch, tmp_path, token=""):
    monkeypatch.setenv("TRADEOS_TOKEN", token)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("""
mode: PAPER
market: { symbol: BTCUSD, resolution: "5m", lookback: 50 }
loop: { poll_seconds: 30 }
strategy: { name: ny_range_fakeout, params: {} }
risk: { account_equity: 10000.0, risk_per_trade_pct: 1.0, stop_loss_pct: 1.5,
        take_profit_pct: 3.0, max_leverage: 5, max_daily_loss_pct: 5.0 }
logging: { level: INFO, trade_log: logs/trades.csv }
""")
    import server
    importlib.reload(server)
    return server


def test_auth_disabled_when_token_unset(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path, token="")
    client = TestClient(server.app)
    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/api/state").status_code == 200


def test_auth_blocks_without_cookie(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path, token="s3cret")
    client = TestClient(server.app)
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/bot/start").status_code == 401


def test_login_wrong_token_rejected(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path, token="s3cret")
    client = TestClient(server.app)
    r = client.post("/login", data={"token": "wrong"})
    assert r.status_code == 401
    assert "tradeos_auth" not in client.cookies


def test_login_correct_token_grants_access(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path, token="s3cret")
    client = TestClient(server.app)
    r = client.post("/login", data={"token": "s3cret"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 200
