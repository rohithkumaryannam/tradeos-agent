"""Functional test for the EMA crossover strategy's logic. Uses small EMA periods
(fast=3, mid=5, trend=20) so the identical cross/trend-filter code can be exercised
on a short oscillating series — it should long in an uptrend and short in a downtrend."""
import math

from strategies.ema_crossover import Strategy
from strategies.base import Action


def _candles():
    closes, base = [], 1000.0
    for i in range(60):                  # rising leg, oscillating -> above trend EMA
        base += 6
        closes.append(base + 40 * math.sin(i))
    for i in range(60):                  # falling leg, oscillating -> below trend EMA
        base -= 6
        closes.append(base + 40 * math.sin(i))
    return [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 3,
             "low": v - 3, "close": v, "volume": 10} for i, v in enumerate(closes)]


def test_ema_crossover_triggers_both_directions():
    candles = _candles()
    s = Strategy(fast=3, mid=5, trend=20)
    actions, pos = set(), None
    for i in range(25, len(candles)):
        sig = s.generate(candles[: i + 1], pos)
        actions.add(sig.action)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
        elif sig.action == Action.ENTER_SHORT:
            pos = "short"
        elif sig.action == Action.EXIT:
            pos = None
    assert Action.ENTER_LONG in actions
    assert Action.ENTER_SHORT in actions


def test_ema_crossover_holds_while_warming_up():
    assert Strategy(trend=200).generate(_candles()[:50], None).action == Action.HOLD
