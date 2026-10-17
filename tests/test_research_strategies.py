"""Rules, no-look-ahead execution, and research boundary checks."""
import math
from types import SimpleNamespace

import pytest

from core.agent import Agent
from core.backtest import simulate
from core.broker import PaperBroker
from core.risk import TradePlan
from core.settings import Settings
from run import ReplayClient
from strategies.base import Action, Signal
from strategies.donchian_breakout import Strategy as Breakout
from strategies.time_series_momentum import Strategy as Momentum
from strategies.loader import available_strategies


def bars(prices, seconds=3600):
    return [{"time": 1_700_000_000+i*seconds, "open": p, "high": p+.1,
             "low": p-.1, "close": p} for i, p in enumerate(prices)]


def breakout():
    return Breakout(entry_period=5, exit_period=3, trend_period=8, atr_period=3)


def momentum():
    return Momentum(horizons=(2,4,6), trend_period=8, atr_period=3)


@pytest.mark.parametrize("factory", [breakout, momentum])
@pytest.mark.parametrize("sign", [1,-1])
def test_direction_and_atr_bracket(factory, sign):
    candles = bars([100+sign*i for i in range(20)])
    sig = factory().generate(candles, None)
    assert sig.action == (Action.ENTER_LONG if sign == 1 else Action.ENTER_SHORT)
    price = candles[-1]["close"]
    assert (price-sig.stop_loss)*sign > 0
    assert (sig.take_profit-price)*sign > 0
    assert abs(sig.take_profit-price) / abs(price-sig.stop_loss) == pytest.approx(3)
    assert sig.reference_price == price and sig.max_entry_deviation > 0


@pytest.mark.parametrize("factory", [breakout, momentum])
def test_warmup_flat_market_and_position_exit(factory):
    strategy = factory()
    assert strategy.generate(bars([100]*3), None).action == Action.HOLD
    assert strategy.generate(bars([100]*30), None).action == Action.HOLD
    sig = strategy.generate(bars([100+i for i in range(20)] + [90]), "long")
    assert sig.action == Action.EXIT and sig.exit_with_bracket


def test_breakout_ignores_current_bar_high_for_channel():
    candles = bars([100+i for i in range(20)])
    candles[-1]["high"] = 200
    assert breakout().generate(candles, None).action == Action.ENTER_LONG


@pytest.mark.parametrize("build", [
    lambda: Breakout(entry_period=0), lambda: Breakout(exit_period=100),
    lambda: Breakout(stop_atr=math.nan), lambda: Momentum(horizons=[]),
    lambda: Momentum(horizons=(6,2)), lambda: Momentum(reward_r=0),
    lambda: Momentum(horizons=(True,4)),
])
def test_invalid_parameters_rejected(build):
    with pytest.raises(ValueError):
        build()


def make_agent(tmp_path, name="donchian_breakout"):
    return Agent(Settings(strategy_name=name, resolution="1h", lookback=300,
                          ai_narrate=False, trade_log=str(tmp_path / "trades.csv")),
                 client=ReplayClient(.001))


def test_live_engine_excludes_unfinished_bar(tmp_path):
    a = make_agent(tmp_path)
    m = a.markets["BTCUSD"]
    candles = bars([100+i*.1 for i in range(220)])
    candles[-1].update(open=500, close=500, high=501, low=499)
    seen = []
    def generate(data, side):
        seen.extend(data)
        return Signal(Action.HOLD, "test")
    m.strategy = SimpleNamespace(closed_candles_only=True, supported_resolutions=("1h",), generate=generate)
    a.fetch_candles = lambda symbol: candles
    a.clock = lambda: candles[-1]["time"] + 30
    a.evaluate()
    assert seen[-1]["time"] == candles[-2]["time"]
    assert all(c["close"] < 500 for c in seen)


def test_price_drift_is_rejected(tmp_path):
    a = make_agent(tmp_path)
    sig = breakout().generate(bars([100+i for i in range(20)]), None)
    out = a._open_from_signal(a.markets["BTCUSD"], sig, sig.reference_price + 10)
    assert out.event == "reject" and "chase" in out.detail


def test_switch_sets_hourly_and_keeps_execution_mode(tmp_path):
    a = make_agent(tmp_path, "ema_crossover")
    a.s.resolution = "1m"
    a.set_strategy("time_series_momentum")
    assert a.s.resolution == "1h" and a.s.mode == "PAPER"
    assert a.s.lookback >= 201
    assert {"donchian_breakout", "time_series_momentum"} <= set(available_strategies())
    assert "_trend" not in available_strategies()


def test_replay_fills_next_open_and_charges_costs(tmp_path):
    a = make_agent(tmp_path, "ema_crossover")
    m = a.markets["BTCUSD"]
    m.strategy = SimpleNamespace(generate=lambda c, side: Signal(Action.ENTER_LONG if not side else Action.HOLD, "test"))
    data = bars([100]*40)
    # First decision is at index 30 close=100; entry at index 31 OPEN=105.
    for b in data[31:]:
        b.update(open=105, high=105.1, low=104.9, close=105)
    report = simulate(a, data, warmup=30, fee_bps=5, slippage_bps=2)
    trade = a.trades[0]
    assert trade["entry"] == pytest.approx(105, abs=.1)  # stored ledger rounds prices
    assert report["fees_paid"] > 0 and report["slippage_cost"] > 0
    assert report["final_equity"] < report["starting_equity"]
    assert report["analytics"]["trades"] == 1


def test_gap_stop_fills_at_gap_price():
    broker = PaperBroker(lambda p: p)
    broker.open(TradePlan("long", 100, 95, 110, 1, 100, 1))
    assert broker.check_bracket({"open":90,"high":94,"low":89,"close":92})[1] == 90


def test_replay_rejects_missing_bars(tmp_path):
    data = bars([100]*260)
    del data[50]
    with pytest.raises(ValueError, match="continuous"):
        simulate(make_agent(tmp_path), data)


def test_new_exit_rule_can_exit_a_bracketed_position(tmp_path):
    a = make_agent(tmp_path)
    m = a.markets["BTCUSD"]
    data = bars([100]*220)
    a.clock = lambda: data[-1]["time"] + 3600
    a.fetch_candles = lambda sym: data
    m.strategy = SimpleNamespace(closed_candles_only=False,
        generate=lambda *args: Signal(Action.EXIT, "channel break", exit_with_bracket=True))
    m.broker.open(TradePlan("long", 100, 90, 120, 10, 100, 1))
    m.broker.position.bracket_confirmed = True
    assert a.evaluate()["BTCUSD"].event == "exit"
