"""Functional tests for the EMA 9/20 crossover strategy (no trend filter) —
EMA9 crossing EMA20 should fire the matching entry/exit regardless of direction."""
from strategies.ema_crossover import Strategy
from strategies.base import Action


def _trend_candles(start, step, n, flat_warmup=10):
    vals = [start] * flat_warmup
    p = start
    for _ in range(n):
        p += step
        vals.append(p)
    return [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 1,
             "low": v - 1, "close": v, "volume": 10} for i, v in enumerate(vals)]


def _run(candles, strat):
    pos, actions = None, []
    for i in range(10, len(candles)):
        sig = strat.generate(candles[: i + 1], pos)
        if sig.action != Action.HOLD:
            actions.append(sig.action)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
        elif sig.action == Action.ENTER_SHORT:
            pos = "short"
        elif sig.action == Action.EXIT:
            pos = None
    return actions


def test_long_on_cross_up():
    candles = _trend_candles(1000.0, +6, 40)   # clean uptrend -> EMA9 crosses above EMA20
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_LONG in actions


def test_short_on_cross_down():
    candles = _trend_candles(1000.0, -5, 40)   # clean downtrend -> EMA9 crosses below EMA20
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_SHORT in actions


def test_exit_fires_on_opposite_cross():
    # downtrend into an uptrend: short entry, then exit on the up-cross
    vals = [1000.0] * 10
    p = 1000.0
    for _ in range(40):
        p -= 5
        vals.append(p)
    for _ in range(40):
        p += 6
        vals.append(p)
    candles = [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 1,
               "low": v - 1, "close": v, "volume": 10} for i, v in enumerate(vals)]
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_SHORT in actions
    assert Action.EXIT in actions


def test_ema_crossover_holds_while_warming_up():
    candles = _trend_candles(1000.0, +6, 2)
    assert Strategy(fast=9, mid=20).generate(candles, None).action == Action.HOLD
