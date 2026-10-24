"""Server-level smoke test for the self-service per-coin strategy endpoint
(POST /api/strategy/sub/{tag}) — the piece the dashboard's new "Assign
strategies per coin" panel calls."""
import importlib

from fastapi.testclient import TestClient


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEOS_TOKEN", "")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("""
mode: PAPER
market: { symbol: [BTCUSD, ETHUSD], resolution: "15m", lookback: 50 }
loop: { poll_seconds: 30 }
strategy:
  name: combo
  params:
    subs:
      - { name: donchian_breakout, tag: donchian_btc, symbols: [BTCUSD] }
      - { name: mean_reversion, tag: mean_reversion, symbols: [ETHUSD] }
risk: { account_equity: 10000.0, risk_per_trade_pct: 1.0, stop_loss_pct: 1.5,
        take_profit_pct: 3.0, max_leverage: 5, max_daily_loss_pct: 5.0,
        strategy_risk_pct: { donchian_btc: 0.25, mean_reversion: 0.25 } }
logging: { level: INFO, trade_log: logs/trades.csv }
""")
    import server
    importlib.reload(server)
    return server


def test_update_sub_endpoint_changes_params_and_risk(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path)
    client = TestClient(server.app)
    r = client.post("/api/strategy/sub/donchian_btc", json={
        "name": "donchian_breakout",
        "params": {"fallback_tp_mult": 2.0},
        "risk_pct": 1.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["sub"]["params"]["fallback_tp_mult"] == 2.0
    assert body["strategy_risk_pct"]["donchian_btc"] == 1.0

    state = client.get("/api/state").json()
    sub = next(s for s in state["strategy_params"]["subs"] if s["tag"] == "donchian_btc")
    assert sub["params"]["fallback_tp_mult"] == 2.0
    assert state["risk"]["strategy_risk_pct"]["donchian_btc"] == 1.0


def test_update_sub_endpoint_rejects_unknown_tag(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path)
    client = TestClient(server.app)
    r = client.post("/api/strategy/sub/not_a_tag", json={"params": {}})
    assert r.status_code == 400
    assert "unknown strategy slot" in r.json()["error"]


def test_update_sub_endpoint_rejects_unknown_param(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path)
    client = TestClient(server.app)
    r = client.post("/api/strategy/sub/donchian_btc", json={
        "params": {"not_a_real_param": 1},
    })
    assert r.status_code == 400
    assert "unknown params" in r.json()["error"]


def test_strategy_catalog_includes_params_for_builder(monkeypatch, tmp_path):
    server = _load_app(monkeypatch, tmp_path)
    client = TestClient(server.app)
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["strategies"]}
    assert "donchian_breakout" in names
    assert "combo" in names  # excluded client-side from the per-sub picker
