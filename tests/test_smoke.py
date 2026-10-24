"""Smoke tests for the TradeOS engine. No network, no exchange — these exercise
the risk engine, the paper broker, and a full backtest run over the bundled
synthetic candles, asserting the safety-critical invariants hold."""
import json
from pathlib import Path

from core.settings import Settings, RiskConfig
from core.risk import RiskEngine, TradePlan
from core.broker import PaperBroker
from core.agent import Agent, _round_price

ROOT = Path(__file__).resolve().parent.parent


def test_sizing_never_exceeds_leverage_cap():
    # A very tight stop would size a huge position; the leverage cap must bite.
    cfg = RiskConfig(account_equity=10_000, risk_per_trade_pct=5.0,
                     stop_loss_pct=0.05, max_leverage=5)
    r = RiskEngine(cfg)
    plan = r.size_position("long", 50_000, contract_value=0.001 * 50_000)
    assert plan.ok
    assert plan.leverage <= cfg.max_leverage + 1e-9


def test_daily_loss_cap_blocks_new_trades():
    cfg = RiskConfig(account_equity=10_000, max_daily_loss_pct=5.0)
    r = RiskEngine(cfg)
    r.register_pnl(-600)  # 6% loss, past the 5% cap
    ok, why = r.can_trade()
    assert not ok and "daily loss cap" in why


def test_paper_broker_pnl_direction():
    b = PaperBroker(lambda p: 0.001 * p)
    plan = TradePlan("long", entry=50_000, stop_loss=49_000, take_profit=52_000,
                     size_contracts=10, notional=10 * 0.001 * 50_000, leverage=1)
    b.open(plan)
    assert b.close(51_000) > 0          # long into a higher price -> profit
    b.open(plan)
    assert b.close(49_500) < 0          # long into a lower price  -> loss


def test_backtest_over_synthetic_is_finite(tmp_path):
    s = Settings()
    s.mode = "PAPER"
    s.strategy_name = "ema_crossover"
    s.strategy_params = {}
    s.trade_log = str(tmp_path / "trades.csv")
    agent = Agent(s)

    candles = json.loads((ROOT / "data" / "synthetic.json").read_text())
    for i in range(30, len(candles)):
        window = candles[: i + 1]
        agent.client.get_candles = lambda *a, _w=window, **k: _w
        agent.evaluate()

    snap = agent.snapshot()
    assert snap["active_strategies"] == ["ema_crossover"]
    assert isinstance(snap["equity"], float) and snap["equity"] > 0
    assert snap["venue"] == "Paper (simulated fills)"


def test_round_price_scales_decimals_to_magnitude():
    # A flat round(p, 1) used to collapse sub-$1 prices to noise — DOGE-like
    # 0.07894 -> 0.1, or a micro-cap like 0.005018 (1000SHIBUSD) -> 0.0 —
    # which corrupted the trade journal's entry/exit prices for any coin
    # trading under $1.
    assert _round_price(79_688.456) == 79_688.5      # BTC-scale: 1 decimal
    assert _round_price(1.2895) == 1.29               # XRP-scale: 2 decimals
    assert _round_price(0.07894) == 0.0789            # DOGE-scale: 4 decimals
    assert _round_price(0.005018) == 0.005018         # SHIB-scale: 6 decimals
    assert _round_price(None) is None


def test_trade_journal_entry_exit_keep_precision_for_cheap_coins(tmp_path):
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    agent = Agent(s)
    trade = agent._mk_trade(
        "1000SHIBUSD",
        {"entry_ts": None, "side": "short", "entry": 0.005018, "size": 15, "sl": None},
        None, 0.005070, -1.42, "manual",
    )
    assert trade["entry"] == 0.005018
    assert trade["exit"] == 0.005070
    assert trade["pnl"] == -1.42


def test_trade_analytics_and_r_multiple(tmp_path):
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    agent = Agent(s)
    # a +2R winner and a -1R loser, both longs, stop $100 away
    agent.trades = [
        agent._mk_trade("BTCUSD", {"entry_ts": None, "side": "long", "entry": 50_000,
                         "size": 10, "sl": 49_900, "tp": 50_200},
                        None, 50_200, 180.0, "take-profit hit"),
        agent._mk_trade("BTCUSD", {"entry_ts": None, "side": "long", "entry": 50_000,
                         "size": 10, "sl": 49_900, "tp": 50_200},
                        None, 49_900, -90.0, "stop-loss hit"),
    ]
    a = agent._analytics()
    assert a["trades"] == 2 and a["wins"] == 1 and a["losses"] == 1
    assert a["win_rate"] == 50.0
    assert a["by_reason"] == {"TP": 1, "SL": 1}
    assert agent.trades[0]["r"] == 2.0 and agent.trades[1]["r"] == -1.0


def test_snapshot_never_claims_paper_protection_as_verified(tmp_path):
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    agent = Agent(s)
    m = agent.markets["BTCUSD"]
    plan = agent.risk.size_position("long", 50_000, contract_value=0.001 * 50_000)
    m.broker.open(plan)
    m.last_candles = [{"open": 50_000, "high": 50_000, "low": 50_000, "close": 50_000}]
    pos = agent.snapshot()["markets"]["BTCUSD"]["position"]
    assert pos["protection"] == "local"          # paper is enforced locally
    assert pos["reconciliation"] == "n/a (simulated)"
