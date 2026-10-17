import math
import csv
import threading
import time
from unittest.mock import Mock

import pytest

from core.agent import Agent, Outcome
from core.broker import DeltaBroker
from core.delta_client import DeltaError
from core.risk import RiskEngine
from core.settings import RiskConfig, Settings
from strategies.base import Action, Signal


def agent(tmp_path):
    return Agent(Settings(symbols=["BTCUSD", "ETHUSD"], ai_narrate=False,
                          trade_log=str(tmp_path / "trades.csv")))


def candles(stamp=None):
    stamp = time.time() if stamp is None else stamp
    return [{"time": stamp - (9-i)*300, "open": 100, "high": 101,
             "low": 99, "close": 100} for i in range(10)]


@pytest.mark.parametrize("entry,cv,sl,tp", [
    (0, 1, 90, 120), (math.nan, 1, 90, 120), (100, 0, 90, 120),
    (100, 1, 110, 120), (100, 1, 90, 80), (100, 1, 90, None),
    (100, 1, 90, math.inf),
])
def test_rejects_invalid_plans(entry, cv, sl, tp):
    assert not RiskEngine(RiskConfig()).size_position(
        "long", entry, cv, stop_loss=sl, take_profit=tp).ok


def test_portfolio_caps_size_against_remaining_budget():
    r = RiskEngine(RiskConfig(max_leverage=2, max_portfolio_risk_pct=2))
    plan = r.size_position("long", 100, 1, stop_loss=99, take_profit=102,
                           open_notional=19_950, open_risk=190, open_positions=2)
    assert plan.ok
    assert plan.notional <= 50
    assert plan.size_contracts * .01 <= 10
    assert not r.size_position("long", 100, 1, open_risk=200).ok
    assert not r.size_position("long", 100, 1, open_positions=3).ok


def test_strategy_risk_reward_and_correlation_limits():
    cfg = RiskConfig(account_equity=10_000, risk_per_trade_pct=0.5,
                     strategy_risk_pct={"ema_crossover": 0.25},
                     max_same_direction_positions=1, min_reward_risk=1.5,
                     max_stop_distance_pct=5)
    r = RiskEngine(cfg)
    plan = r.size_position("long", 100, 1, stop_loss=99, take_profit=102,
                           risk_pct=cfg.risk_for("ema_crossover"))
    assert plan.ok and plan.size_contracts == 2500
    assert not r.size_position("long", 100, 1, stop_loss=99, take_profit=101,
                               risk_pct=0.25).ok
    assert not r.size_position("long", 100, 1, stop_loss=90, take_profit=120,
                               risk_pct=0.25).ok
    assert not r.size_position("long", 100, 1, same_direction_positions=1,
                               risk_pct=0.25).ok


def test_dashboard_risk_controls_update_engine_atomically(tmp_path):
    a = agent(tmp_path)
    a.s.risk.strategy_risk_pct = {"ema_crossover": 0.25, "donchian_breakout": 0.5}
    updated = a.update_risk_controls({
        "max_portfolio_risk_pct": 2,
        "max_open_positions": 3,
        "max_same_direction_positions": 2,
        "max_leverage": 4,
        "max_daily_loss_pct": 3,
        "strategy_risk_pct": {"ema_crossover": 0.4, "donchian_breakout": 0.75},
    })
    assert updated is a.s.risk is a.risk.cfg
    assert updated.max_open_positions == 3
    assert updated.risk_per_trade_pct == 0.75
    assert updated.risk_for("ema_crossover") == 0.4
    with pytest.raises(ValueError, match="same-direction"):
        a.update_risk_controls({
            "max_portfolio_risk_pct": 2, "max_open_positions": 1,
            "max_same_direction_positions": 2, "max_leverage": 4,
            "max_daily_loss_pct": 3,
            "strategy_risk_pct": {"ema_crossover": 0.4, "donchian_breakout": 0.75},
        })
    assert a.s.risk is updated


def test_pause_blocks_entries_but_allows_exit(tmp_path):
    a = agent(tmp_path)
    m = a.markets["BTCUSD"]
    a._open_from_signal(m, Signal(Action.ENTER_LONG, "test"), 100)
    assert m.broker.position
    a.pause_entries(True)
    assert a._open_from_signal(a.markets["ETHUSD"], Signal(Action.ENTER_LONG, "test"), 100).event == "reject"
    assert a.force_close("BTCUSD").event == "exit"


def test_stale_data_and_expired_manual_signals_block_entries(tmp_path):
    a = agent(tmp_path)
    m = a.markets["BTCUSD"]
    m.strategy = Mock(closed_candles_only=False, generate=lambda *args: Signal(Action.ENTER_LONG, "test"))
    a.fetch_candles = lambda sym: candles(1)
    assert a.evaluate()["BTCUSD"].event == "reject"
    assert not m.broker.position
    a.set_auto(False)
    a.fetch_candles = lambda sym: candles()
    assert a.evaluate()["BTCUSD"].event == "pending"
    m.pending_at -= 1000
    assert a.execute_pending("BTCUSD").event == "reject"
    assert not m.broker.position


def test_one_market_failure_does_not_skip_other_markets(tmp_path):
    a = agent(tmp_path)
    def evaluate(m):
        if m.symbol == "BTCUSD":
            raise DeltaError("offline")
        return Outcome("hold", "evaluated")
    a._evaluate_market = evaluate
    out = a.evaluate()
    assert out["BTCUSD"].event == "error"
    assert out["ETHUSD"].detail == "evaluated"


def broker_client():
    client = Mock()
    client.get_product.return_value = {"id": 1, "contract_value": .001}
    client.get_positions.return_value = []
    client.has_bracket.return_value = True
    return client


def test_ambiguous_order_never_retries_and_latches_entries():
    client = broker_client()
    b = DeltaBroker(client, "BTCUSD")
    client.place_order.side_effect = DeltaError("timeout after submission")
    plan = RiskEngine(RiskConfig()).size_position("long", 100, 1)
    with pytest.raises(DeltaError):
        b.open(plan)
    with pytest.raises(DeltaError):
        b.open(plan)
    assert client.place_order.call_count == 1
    assert len(client.place_order.call_args.kwargs["client_order_id"]) == 32
    assert b.execution_issue


def test_bracket_lookup_failure_preserves_accepted_position():
    client = broker_client()
    b = DeltaBroker(client, "BTCUSD")
    client.has_bracket.side_effect = DeltaError("lookup unavailable")
    b.open(RiskEngine(RiskConfig()).size_position("long", 100, 1))
    assert b.position and not b.position.bracket_confirmed
    assert client.place_order.call_count == 1


def test_reconcile_failure_blocks_entry():
    client = broker_client()
    client.get_positions.side_effect = DeltaError("offline")
    b = DeltaBroker(client, "BTCUSD")
    with pytest.raises(DeltaError):
        b.open(RiskEngine(RiskConfig()).size_position("long", 100, 1))
    client.place_order.assert_not_called()


def test_failed_mode_switch_is_atomic(tmp_path):
    a = agent(tmp_path)
    a.s.api_key = a.s.api_secret = "test"
    old = a.markets["BTCUSD"].broker
    a.client.get_product = Mock(side_effect=DeltaError("offline"))
    with pytest.raises(DeltaError):
        a.set_mode("DELTA_DEMO")
    assert a.s.mode == "PAPER" and a.markets["BTCUSD"].broker is old


def test_runner_refuses_overlapping_restart(monkeypatch):
    from server import BotRunner
    entered, release = threading.Event(), threading.Event()
    a = Mock()
    a.s.poll_seconds = 30
    def evaluate():
        entered.set()
        release.wait(2)
    a.evaluate = Mock(side_effect=evaluate)
    r = BotRunner(a)
    try:
        r.start()
        assert entered.wait(1)
        r.stop()
        with pytest.raises(RuntimeError):
            r.start()
        assert a.evaluate.call_count == 1
    finally:
        release.set()
        r._thread.join(2)
    assert not r._thread.is_alive()


def test_replay_executes_trades_offline_and_accounts_for_fees(tmp_path):
    from run import ReplayClient, backtest
    s = Settings(strategy_name="ema_crossover", strategy_params={"fast": 3, "mid": 8, "trend": 0},
                 ai_narrate=False, trade_log=str(tmp_path / "replay.csv"))
    a = Agent(s, client=ReplayClient(.001))
    data = []
    for i in range(160):
        price = 100 + 10 * math.sin(i / 8)
        data.append({"time": 1_700_000_000 + i*300, "open": price,
                     "close": price, "high": price+1, "low": price-1})
    report = backtest(a, data, fee_bps=5)
    assert report["analytics"]["trades"] > 0
    assert report["final_equity"] == pytest.approx(10_000 + sum(t["pnl"] for t in a.trades), abs=.02)
    assert all(t["entry_ts"].startswith("2023-") for t in a.trades)
    assert not a.markets["BTCUSD"].broker.position
    with pytest.raises(ValueError, match="offline"):
        backtest(agent(tmp_path), data)


def test_same_candle_does_not_reenter_or_use_preentry_extremes(tmp_path):
    a = agent(tmp_path)
    m = a.markets["BTCUSD"]
    data = candles()
    data[-1].update(high=120, low=80)
    a.fetch_candles = lambda symbol: data
    m.strategy = Mock(closed_candles_only=False, generate=lambda *args: Signal(Action.ENTER_LONG, "test"))
    assert a.evaluate()["BTCUSD"].event == "entry"
    assert a.evaluate()["BTCUSD"].event == "hold"
    a.force_close("BTCUSD")
    assert a.evaluate()["BTCUSD"].event == "reject"


def test_daily_budget_rolls_at_utc_midnight():
    from datetime import datetime, timezone
    now = [datetime(2026, 10, 16, 23, 59, tzinfo=timezone.utc).timestamp()]
    r = RiskEngine(RiskConfig(), clock=lambda: now[0])
    r.register_pnl(-600)
    assert not r.can_trade()[0]
    now[0] += 120
    assert r.can_trade()[0]
    assert r.realized_today == 0
    assert r._day_start_equity == 9400


def test_pause_endpoint_validates_and_persists():
    from fastapi.testclient import TestClient
    from core import state
    import server
    server.AUTH_TOKEN = ""
    client = TestClient(server.app)
    assert client.post("/api/entries/pause").json() == {"entries_paused": True}
    assert state.load()["entries_paused"] is True
    assert client.get("/api/state").json()["entries_paused"] is True
    assert client.post("/api/entries/invalid").status_code == 400
    assert client.post("/api/entries/resume").json() == {"entries_paused": False}


def test_replay_mode_override_does_not_require_exchange_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    monkeypatch.delenv("DELTA_API_SECRET", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text('mode: DELTA_DEMO\nmarket: {symbol: BTCUSD, resolution: 5m}\nloop: {}\nstrategy: {name: ema_crossover}\n')
    assert Settings.load(config, mode_override="PAPER").mode == "PAPER"


def test_confirmed_exchange_bracket_ignores_old_candle_extremes():
    client = broker_client()
    b = DeltaBroker(client, "BTCUSD")
    b.open(RiskEngine(RiskConfig()).size_position("long", 100, 1))
    client.get_positions.return_value = [{"product_id": 1, "size": 1}]
    assert b.check_bracket({"high": 200, "low": 1, "close": 100}) is None


def test_account_circuit_breaker_recovers_and_reconciles_without_error_spam(tmp_path):
    now = [1000.0]
    error = DeltaError(
        'GET /v2/wallet/balances -> 401: {"error":{"code":'
        '"ip_not_whitelisted_for_api_key","context":{"client_ip":"49.43.223.162"}},'
        '"success":false}'
    )
    client = Mock()
    product_ids = {"BTCUSD": 1, "ETHUSD": 2}
    client.get_product.side_effect = lambda symbol: {
        "id": product_ids[symbol], "contract_value": .001,
    }
    client.get_balance.side_effect = [
        error, error,
        [{"asset_symbol": "USD", "balance": "1000", "available_balance": "900"}],
    ]
    client.get_positions.return_value = [
        {"product_id": 1, "product_symbol": "BTCUSD", "size": "2",
         "entry_price": "50000", "margin": "100"},
    ]
    client.get_bracket_prices.return_value = (49000.0, 52000.0)
    settings = Settings(mode="DELTA_DEMO", symbols=["BTCUSD", "ETHUSD"],
                        ai_narrate=False, trade_log=str(tmp_path / "trades.csv"))
    a = Agent(settings, client=client, clock=lambda: now[0])

    assert a._acct["code"] == "ip_not_whitelisted_for_api_key"
    assert a._acct["client_ip"] == "49.43.223.162"
    assert a._acct["retry_at"] == 1300
    assert client.get_balance.call_count == 1

    now[0] = 1299
    a._refresh_account()
    assert client.get_balance.call_count == 1
    now[0] = 1300
    a._refresh_account()
    assert client.get_balance.call_count == 2
    assert len([e for e in a.recent if e["event"] == "account"]) == 1

    result = a.retry_account()
    assert result["status"] == "live"
    assert a.markets["BTCUSD"].broker.position.size == 2
    assert a.markets["BTCUSD"].broker.position.bracket_confirmed is True
    assert a.markets["ETHUSD"].broker.position is None
    assert [e["detail"] for e in a.recent if e["event"] == "account"][0].endswith(
        "restored automatically")


def test_strategy_resolution_self_heals_on_startup(tmp_path):
    settings = Settings(
        symbols=["BTCUSD"], resolution="1m", strategy_name="combo",
        strategy_params={"subs": ["ema_crossover", "donchian_breakout"]},
        ai_narrate=False, trade_log=str(tmp_path / "trades.csv"),
    )
    a = Agent(settings)
    assert a.s.resolution == "1h"
    assert a.s.lookback >= a.markets["BTCUSD"].strategy.required_candles + 5


def test_demo_strategy_change_is_locked_when_positions_are_unknown(tmp_path):
    a = agent(tmp_path)
    a.s.mode = "DELTA_DEMO"
    a._acct["status"] = "unavailable"
    with pytest.raises(RuntimeError, match="cannot verify positions"):
        a.set_strategy("donchian_breakout")
    snapshot = a.snapshot()
    assert snapshot["portfolio"]["known"] is False
    assert snapshot["entry_risk_ok"] is False
    assert "portfolio exposure unavailable" in snapshot["entry_risk_reason"]


def test_headerless_trade_log_is_repaired_and_loaded(tmp_path):
    path = tmp_path / "trades.csv"
    rows = [
        ["2026-10-17T01:00:00+00:00", "ENTRY", "long", "100", "2", "95", "110", "", "1000", "signal", "ema_crossover", "BTCUSD"],
        ["2026-10-17T02:00:00+00:00", "EXIT", "long", "110", "", "", "", "20", "1020", "take-profit hit", "", "BTCUSD"],
    ]
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    a = Agent(Settings(symbols=["BTCUSD"], ai_narrate=False, trade_log=str(path)))
    assert a.trades[0]["pnl"] == 20
    with path.open(newline="") as f:
        assert next(csv.reader(f))[0:2] == ["ts", "event"]


def test_exchange_fill_completes_unmatched_trade(tmp_path):
    a = agent(tmp_path)
    m = a.markets["BTCUSD"]
    a.s.mode = "DELTA_DEMO"
    m.broker = DeltaBroker(broker_client(), "BTCUSD", reconcile=False)
    m.open_trade = {
        "entry_ts": "2026-10-17T01:00:00+00:00", "side": "short",
        "entry": 100.0, "size": 2, "sl": 105.0, "tp": 90.0,
        "strategy": "test", "contract_value": 0.1,
    }
    a.client.get_fills = Mock(return_value=[{
        "side": "buy", "size": "2", "price": "105",
        "created_at": "2026-10-17T02:00:00Z",
    }])
    m.broker.client = a.client
    assert a._record_exchange_exit(m)
    assert m.open_trade is None
    assert a.trades[-1]["reason"] == "SL"
