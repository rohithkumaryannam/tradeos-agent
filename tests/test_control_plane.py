"""The dashboard's "full access" control plane: engine/risk settings, adding
and removing markets, per-coin on/off, manual trades, flatten-all, the
change history, persistence across restarts, and the guarantee that
backtests never touch the live database."""
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from core import audit, db
from core.agent import Agent
from core.settings import RiskConfig, Settings
from strategies.base import Action


COMBO_SUBS = [
    {"name": "donchian_breakout", "tag": "donchian_btc", "symbols": ["BTCUSD"],
     "params": {"fallback_tp_mult": 8.0}},
    {"name": "donchian_breakout", "tag": "donchian_eth", "symbols": ["ETHUSD"]},
]


def _agent(tmp_path, symbols=("BTCUSD", "ETHUSD")):
    s = Settings(mode="PAPER", symbols=list(symbols), resolution="15m", lookback=60,
                 strategy_name="combo", strategy_params={"subs": [dict(x) for x in COMBO_SUBS]},
                 risk=RiskConfig(risk_per_trade_pct=0.5,
                                 strategy_risk_pct={"donchian_btc": 0.25, "donchian_eth": 0.25}),
                 trade_log=str(tmp_path / "trades.csv"))
    return Agent(s)


def _fresh_candles(price=100.0, n=5):
    now = int(time.time()) // 900 * 900
    return [{"time": now - 900 * (n - 1 - i), "open": price, "high": price * 1.001,
             "low": price * 0.999, "close": price, "volume": 10} for i in range(n)]


# ── backtests can never touch the live database ──
class _RecordingDB:
    def __init__(self):
        self.writes = []

    def __getattr__(self, collection):
        rec = self

        class _Coll:
            def __getattr__(self, op):
                def call(*args, **kwargs):
                    rec.writes.append((collection, op))
                    return None
                return call
        return _Coll()


def test_offline_context_turns_every_db_call_into_a_noop(monkeypatch):
    fake = _RecordingDB()
    monkeypatch.setattr(db, "_connect", lambda: fake)
    db.save_position_bracket("BTCUSD", {"stop_loss": 1})
    assert fake.writes == [("position_brackets", "update_one")]
    with db.offline():
        assert db.is_offline()
        db.save_position_bracket("BTCUSD", {"stop_loss": 1})
        db.clear_position_bracket("BTCUSD")
        db.insert_trade({"event": "ENTRY"})
        assert db.load_trades() is None
    assert not db.is_offline()
    assert fake.writes == [("position_brackets", "update_one")]


def test_simulate_never_writes_live_position_brackets(monkeypatch, tmp_path):
    from core.backtest import simulate
    from core.backtest_lab import _ReplayClient

    fake = _RecordingDB()
    monkeypatch.setattr(db, "_connect", lambda: fake)
    s = Settings(mode="PAPER", symbols=["BTCUSD"], resolution="15m", lookback=60,
                 strategy_name="donchian_breakout", strategy_params={"entry_period": 5, "exit_period": 3,
                                                                    "use_adx_filter": False, "volume_mult": 0.1},
                 ai_narrate=False, risk=RiskConfig(account_equity=10_000, risk_per_trade_pct=0.5),
                 trade_log=str(tmp_path / "t.csv"))
    agent = Agent(s, client=_ReplayClient(0.001))
    start = 1_700_000_000 // 900 * 900
    candles, price = [], 100.0
    for i in range(400):  # a zig-zag that breaks out repeatedly
        price *= 1.004 if (i // 25) % 2 == 0 else 0.996
        candles.append({"time": start + 900 * i, "open": price, "high": price * 1.003,
                        "low": price * 0.997, "close": price, "volume": 100 + i})
    report = simulate(agent, candles, warmup=59, bracket_owns_exit=True)
    assert report["analytics"]["trades"] > 0, "fixture should trade"
    assert fake.writes == [], f"backtest touched the live database: {fake.writes[:5]}"
    assert report["bracket_owns_exit"] is True
    assert report["equity_samples"]


# ── engine + risk settings ──
def test_update_engine_validates_and_applies(tmp_path):
    a = _agent(tmp_path)
    out = a.update_engine({"poll_seconds": 20, "ai_narrate": False})
    assert out["poll_seconds"] == 20 and out["ai_narrate"] is False
    with pytest.raises(ValueError):
        a.update_engine({"poll_seconds": 2})
    with pytest.raises(ValueError):
        a.update_engine({"lookback": 99.5})
    with pytest.raises(ValueError):
        a.update_engine({"resolution": "7m"})


def test_update_risk_settings_is_partial_and_validated(tmp_path):
    a = _agent(tmp_path)
    a.update_risk_settings({"stop_loss_pct": 1.2, "min_reward_risk": 2})
    assert a.s.risk.stop_loss_pct == 1.2 and a.s.risk.min_reward_risk == 2
    assert a.risk.cfg is a.s.risk
    a.update_risk_settings({"strategy_risk_pct": {"donchian_btc": 1.5}})
    assert a.s.risk.strategy_risk_pct["donchian_btc"] == 1.5
    assert a.s.risk.risk_per_trade_pct == 1.5          # ceiling follows the largest cap
    with pytest.raises(ValueError):
        a.update_risk_settings({"max_open_positions": 2.5})
    with pytest.raises(ValueError):
        a.update_risk_settings({"max_open_positions": 1, "max_same_direction_positions": 3})
    with pytest.raises(ValueError):
        a.update_risk_settings({"strategy_risk_pct": {"no_such_slot": 1}})
    # boot restore tolerates caps for slots that no longer exist
    a.update_risk_settings({"strategy_risk_pct": {"no_such_slot": 1, "donchian_eth": 0.3}}, strict=False)
    assert a.s.risk.strategy_risk_pct["donchian_eth"] == 0.3
    assert "no_such_slot" not in a.s.risk.strategy_risk_pct


# ── markets ──
def test_add_and_remove_market(tmp_path):
    a = _agent(tmp_path)
    sub = a.add_market("ltcusd", strategy="donchian_breakout", params={"fallback_tp_mult": 4}, risk_pct=0.3)
    assert "LTCUSD" in a.markets and a.s.symbols[-1] == "LTCUSD"
    assert sub["tag"] == "donchian_ltc" and sub["symbols"] == ["LTCUSD"]
    assert a.s.risk.strategy_risk_pct["donchian_ltc"] == 0.3
    # its own combo instance only runs its own slot
    assert [tag for tag, _ in a.markets["LTCUSD"].strategy.subs] == ["donchian_ltc"]
    with pytest.raises(ValueError):
        a.add_market("LTCUSD", strategy="donchian_breakout")
    with pytest.raises(ValueError):
        a.add_market("BAD-SYMBOL!", strategy="donchian_breakout")
    with pytest.raises(ValueError):
        a.add_market("ADAUSD", strategy="combo")

    a.remove_market("LTCUSD")
    assert "LTCUSD" not in a.markets
    assert all(s.get("tag") != "donchian_ltc" for s in a.s.strategy_params["subs"])
    assert "donchian_ltc" not in a.s.risk.strategy_risk_pct


def test_remove_market_refused_with_open_position(tmp_path):
    a = _agent(tmp_path)
    m = a.markets["ETHUSD"]
    m.last_candles = _fresh_candles(3000)
    m.last_fetch = time.time()
    out = a.manual_entry("ETHUSD", "long")
    assert out.event == "entry", out.detail
    with pytest.raises(ValueError, match="close the open"):
        a.remove_market("ETHUSD")


def test_last_market_cannot_be_removed(tmp_path):
    a = _agent(tmp_path, symbols=("BTCUSD",))
    with pytest.raises(ValueError):
        a.remove_market("BTCUSD")


# ── per-coin on/off ──
def test_disabled_slot_takes_no_entries_but_keeps_params(tmp_path):
    a = _agent(tmp_path)
    a.update_sub("donchian_btc", enabled=False)
    slot = next(s for s in a.slots() if s["tag"] == "donchian_btc")
    assert slot["enabled"] is False
    assert slot["params"]["fallback_tp_mult"] == 8.0
    (tag, strat), = a.markets["BTCUSD"].strategy.subs
    assert tag == "donchian_btc"
    assert strat.generate([], None).action == Action.HOLD
    assert "paused" in strat.generate([], None).reason
    a.update_sub("donchian_btc", enabled=True)
    assert next(s for s in a.slots() if s["tag"] == "donchian_btc")["enabled"] is True


def test_reset_params_restores_strategy_defaults(tmp_path):
    a = _agent(tmp_path)
    a.update_sub("donchian_btc", reset_params=True)
    slot = next(s for s in a.slots() if s["tag"] == "donchian_btc")
    assert slot["params"] == {}
    assert slot["effective_params"]["fallback_tp_mult"] == 8.0  # the strategy default


# ── manual trading + flatten ──
def test_manual_entry_is_risk_sized_and_bracketed(tmp_path):
    a = _agent(tmp_path)
    m = a.markets["BTCUSD"]
    m.last_candles = _fresh_candles(50_000)
    m.last_fetch = time.time()
    out = a.manual_entry("BTCUSD", "short", sl_pct=1, tp_pct=3)
    assert out.event == "entry", out.detail
    pos = m.broker.position
    assert pos.side == "short"
    assert pos.stop_loss == pytest.approx(50_500) and pos.take_profit == pytest.approx(48_500)
    assert m.open_trade["strategy"] == "manual"
    assert a.manual_entry("BTCUSD", "long").event == "reject"
    with pytest.raises(ValueError):
        a.manual_entry("ETHUSD", "sideways")
    with pytest.raises(ValueError):
        a.manual_entry("ETHUSD", "long", sl_pct=50)       # beyond max_stop_distance_pct


def test_manual_entry_respects_reward_risk_floor(tmp_path):
    a = _agent(tmp_path)
    m = a.markets["BTCUSD"]
    m.last_candles = _fresh_candles(50_000)
    m.last_fetch = time.time()
    out = a.manual_entry("BTCUSD", "long", sl_pct=2, tp_pct=1)
    assert out.event == "reject"
    assert m.broker.position is None


def test_close_all_flattens_every_market(tmp_path):
    a = _agent(tmp_path)
    for sym, px in (("BTCUSD", 50_000), ("ETHUSD", 3_000)):
        m = a.markets[sym]
        m.last_candles = _fresh_candles(px)
        m.last_fetch = time.time()
        assert a.manual_entry(sym, "long").event == "entry"
    results = a.close_all()
    assert set(results) == {"BTCUSD", "ETHUSD"}
    assert all(m.broker.position is None for m in a.markets.values())
    assert len(a.trades) == 2 and all(t["strategy"] == "manual" for t in a.trades)
    assert a.trades[0]["entry_note"].startswith("manual long")


# ── jarvis can reach the same controls ──
def test_jarvis_coin_actions(tmp_path):
    a = _agent(tmp_path)
    a.jarvis_execute("set_coin_params", {"symbol": "ETHUSD", "params": {"fallback_tp_mult": 4}})
    assert next(s for s in a.slots() if s["tag"] == "donchian_eth")["params"]["fallback_tp_mult"] == 4.0
    a.jarvis_execute("set_coin_enabled", {"symbol": "ETHUSD", "enabled": False})
    assert next(s for s in a.slots() if s["tag"] == "donchian_eth")["enabled"] is False
    a.jarvis_execute("set_coin_risk", {"symbol": "ETHUSD", "risk_pct": 0.4})
    assert a.s.risk.strategy_risk_pct["donchian_eth"] == 0.4
    a.jarvis_execute("set_risk_limit", {"field": "max_open_positions", "value": 4})
    assert a.s.risk.max_open_positions == 4
    with pytest.raises(ValueError):
        a.jarvis_execute("set_risk_limit", {"field": "api_secret", "value": 1})
    with pytest.raises(ValueError):
        a.jarvis_execute("set_coin_params", {"symbol": "NOPEUSD", "params": {"x": 1}})


def test_lite_snapshot_skips_charts_but_keeps_sparklines(tmp_path):
    a = _agent(tmp_path)
    a.markets["BTCUSD"].last_candles = _fresh_candles(50_000, n=100)
    snap = a.snapshot(lite=True)
    btc = snap["markets"]["BTCUSD"]
    assert btc["chart"] == []
    assert len(btc["spark"]) == 72 and btc["candle"]["c"] == 50_000
    assert btc["change_24h"] == 0.0
    assert {s["tag"] for s in snap["slots"]} == {"donchian_btc", "donchian_eth"}
    full = a.snapshot()
    assert len(full["markets"]["BTCUSD"]["chart"]) == 100


# ── server endpoints, persistence and change history ──
def _server(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEOS_TOKEN", "")
    monkeypatch.chdir(tmp_path)
    import server
    importlib.reload(server)
    return server


def test_config_endpoint_describes_everything(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    cfg = client.get("/api/config").json()
    for key in ("engine", "risk", "risk_limits", "slots", "symbols", "timeframes", "alerts", "version"):
        assert key in cfg
    assert cfg["risk_limits"]["max_open_positions"][2] == "int"
    catalog = client.get("/api/strategies").json()["strategies"]
    donchian = next(s for s in catalog if s["name"] == "donchian_breakout")
    assert donchian["description"] and donchian["params"]["fallback_tp_mult"] == 8.0


def test_changes_persist_and_are_audited(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    tag = server.agent.slots()[0]["tag"]
    assert client.post("/api/config/risk", json={"stop_loss_pct": 1.1}).status_code == 200
    assert client.post("/api/config/engine", json={"poll_seconds": 25}).status_code == 200
    assert client.post(f"/api/strategy/sub/{tag}", json={"enabled": False}).status_code == 200
    r = client.post("/api/markets", json={"symbol": "LTCUSD", "strategy": "donchian_breakout", "risk_pct": 0.2})
    assert r.status_code == 200, r.text
    bad = client.post("/api/config/risk", json={"max_open_positions": 0})
    assert bad.status_code == 400 and "max_open_positions" in bad.json()["error"]

    saved = server.state.load()
    assert saved["risk"]["stop_loss_pct"] == 1.1
    assert saved["engine"]["poll_seconds"] == 25
    assert "LTCUSD" in saved["symbols"]
    assert any(s.get("tag") == tag and s.get("enabled") is False for s in saved["strategy_subs"])

    actions = [e["action"] for e in client.get("/api/audit").json()["entries"]]
    assert actions[:4] == ["market.add", "slot.update", "engine.update", "risk.update"]

    # a restart rebuilds the added market and the disabled slot from saved state
    server2 = _server(monkeypatch, tmp_path)
    server2._resume_from_state()
    assert "LTCUSD" in server2.agent.markets
    assert server2.agent.s.poll_seconds == 25
    assert server2.agent.s.risk.stop_loss_pct == 1.1
    assert next(s for s in server2.agent.slots() if s["tag"] == tag)["enabled"] is False


def test_config_revision_bump_discards_saved_markets(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    client.post("/api/markets", json={"symbol": "LTCUSD", "strategy": "donchian_breakout"})
    saved = server.state.load()
    server.state.save(config_revision=saved["config_revision"] - 1)
    server2 = _server(monkeypatch, tmp_path)
    assert "LTCUSD" not in server2.agent.markets
    server2._resume_from_state()
    assert "symbols" not in server2.state.load()


def test_manual_trade_and_flatten_endpoints(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    sym = next(iter(server.agent.markets))
    m = server.agent.markets[sym]
    m.last_candles = _fresh_candles(100.0)
    m.last_fetch = time.time()
    r = client.post("/api/trade", json={"symbol": sym, "side": "long", "sl_pct": 1, "tp_pct": 2})
    assert r.status_code == 200, r.text
    assert client.post("/api/trade", json={"symbol": sym, "side": "long"}).status_code == 409
    assert client.post("/api/trade", json={"symbol": sym, "side": "up"}).status_code == 400
    out = client.post("/api/positions/close-all", json={"pause_entries": True}).json()
    assert sym in out["results"] and out["entries_paused"] is True
    trades = client.get("/api/trades").json()
    assert trades["count"] == 1 and trades["trades"][0]["strategy"] == "manual"


def test_config_export_is_loadable_yaml(monkeypatch, tmp_path):
    import yaml
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    r = client.get("/api/config/export")
    assert r.status_code == 200
    doc = yaml.safe_load(r.text)
    assert doc["market"]["symbol"] == list(server.agent.markets)
    assert doc["strategy"]["name"] == server.agent.s.strategy_name
    assert "api_key" not in r.text and "MONGODB" not in r.text


def test_backtest_validation_rejects_bad_requests(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    assert client.post("/api/backtest", json={"symbol": "BTCUSD", "strategy": "combo"}).status_code == 400
    assert client.post("/api/backtest", json={"symbol": "BTCUSD", "strategy": "donchian_breakout",
                                              "days": 9999}).status_code == 400
    assert client.post("/api/backtest", json={"symbol": "BTCUSD", "strategy": "donchian_breakout",
                                              "params": {"nope": 1}}).status_code == 400


def test_lite_websocket_stream(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    with client.websocket_connect("/ws?lite=1") as ws:
        snap = json.loads(ws.receive_text())
    assert "slots" in snap and "server" in snap
    assert all(m["chart"] == [] for m in snap["markets"].values())


def test_classic_dashboard_still_served(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    assert "TradeOS" in client.get("/classic").text
    assert client.get("/trade/BTCUSD").status_code == 200


def test_reset_plus_params_sets_exact_overrides(tmp_path):
    """The dashboard saves a slot as reset_params + only the non-default
    values, so a value moved back to its default stops being an override."""
    a = _agent(tmp_path)
    a.update_sub("donchian_btc", params={"fallback_tp_mult": 2.0, "entry_period": 30})
    a.update_sub("donchian_btc", reset_params=True, params={"entry_period": 30})
    slot = next(s for s in a.slots() if s["tag"] == "donchian_btc")
    assert slot["params"] == {"entry_period": 30}
    assert slot["effective_params"]["fallback_tp_mult"] == 8.0


def test_bad_saved_risk_does_not_block_resume(monkeypatch, tmp_path):
    """Every restore step is independent: an invalid saved risk value is
    skipped, and the bot still resumes running."""
    server = _server(monkeypatch, tmp_path)
    server.state.save(risk={"max_open_positions": -5}, running=True, entries_paused=True,
                      config_revision=server.CONFIG_REVISION)
    server2 = _server(monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr(server2.runner, "start", lambda: started.append(True))
    server2._resume_from_state()
    assert started == [True]
    assert server2.agent.entries_paused is True


def test_rejected_manual_order_explains_why(monkeypatch, tmp_path):
    server = _server(monkeypatch, tmp_path)
    client = TestClient(server.app)
    sym = next(iter(server.agent.markets))
    m = server.agent.markets[sym]
    m.last_candles = _fresh_candles(100.0)
    m.last_fetch = time.time()
    r = client.post("/api/trade", json={"symbol": sym, "side": "long", "sl_pct": 2, "tp_pct": 1})
    assert r.status_code == 409
    body = r.json()
    assert body["result"]["event"] == "reject"
    assert body["error"] == body["result"]["detail"] and body["error"]


def test_manual_risk_cannot_exceed_per_trade_ceiling(tmp_path):
    a = _agent(tmp_path)                       # caps 0.25 → ceiling 0.5 (config)
    m = a.markets["BTCUSD"]
    m.last_candles = _fresh_candles(50_000)
    m.last_fetch = time.time()
    with pytest.raises(ValueError, match="ceiling"):
        a.manual_entry("BTCUSD", "long", risk_pct=2)
    assert a.manual_entry("BTCUSD", "long", risk_pct=0.5).event == "entry"
