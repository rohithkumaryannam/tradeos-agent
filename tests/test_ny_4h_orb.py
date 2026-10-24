from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.ny_4h_orb import Strategy
from strategies.base import Action

NY = ZoneInfo("America/New_York")


def _ts(year, month, day, hour, minute):
    return int(datetime(year, month, day, hour, minute, tzinfo=NY).timestamp())


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _opening_range_candles(day=(2026, 1, 5), high=105.0, low=95.0):
    """5m candles covering the 00:00-04:00 NY window for the given day,
    with a known high/low."""
    y, m, d = day
    candles = []
    t = _ts(y, m, d, 0, 0)
    for i in range(48):  # 4 hours of 5m bars
        price = 100.0
        h, l = 100.5, 99.5
        if i == 10:
            h = high
        if i == 20:
            l = low
        candles.append(_candle(t + i * 300, price, h, l, price))
    return candles


def test_range_still_forming_inside_window():
    strat = Strategy()
    candles = _opening_range_candles()[:20]  # still within 00:00-04:00
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "forming" in sig.reason or "warming up" in sig.reason


def test_long_entry_on_close_above_range_high():
    strat = Strategy(reward_risk=2.0)
    candles = _opening_range_candles(high=105.0, low=95.0)
    # advance into the trading window (past 04:00 NY) with a breakout close
    t = candles[-1]["time"] + 300
    candles.append(_candle(t, 100, 106, 100, 106))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss == 105.0
    assert sig.take_profit - 106.0 == 2.0 * (106.0 - 105.0)


def test_short_entry_on_close_below_range_low():
    strat = Strategy(reward_risk=2.0)
    candles = _opening_range_candles(high=105.0, low=95.0)
    t = candles[-1]["time"] + 300
    candles.append(_candle(t, 100, 100, 94, 94))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss == 95.0
    assert sig.take_profit == 94.0 - 2.0 * (95.0 - 94.0)


def test_hold_inside_range_after_window():
    strat = Strategy()
    candles = _opening_range_candles(high=105.0, low=95.0)
    t = candles[-1]["time"] + 300
    candles.append(_candle(t, 100, 101, 99, 100))
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD


def test_hold_in_position():
    strat = Strategy()
    candles = _opening_range_candles()
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD
