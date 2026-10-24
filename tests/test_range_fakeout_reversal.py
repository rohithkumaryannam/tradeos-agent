from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.range_fakeout_reversal import Strategy
from strategies.base import Action

NY = ZoneInfo("America/New_York")


def _ts(year, month, day, hour, minute):
    return int(datetime(year, month, day, hour, minute, tzinfo=NY).timestamp())


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _opening_range(day=(2026, 1, 5), high=105.0, low=95.0):
    y, m, d = day
    candles = []
    t = _ts(y, m, d, 0, 0)
    for i in range(48):
        h, l = 100.5, 99.5
        if i == 10:
            h = high
        if i == 20:
            l = low
        candles.append(_candle(t + i * 300, 100, h, l, 100))
    return candles


def test_warming_up():
    strat = Strategy()
    candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD


def test_short_fade_on_fakeout_above_range_high():
    strat = Strategy(reward_risk=2.0)
    candles = _opening_range(high=105.0, low=95.0)
    t = candles[-1]["time"]
    # breakout candle: closes above range high
    t += 300
    candles.append(_candle(t, 100, 107, 100, 106))
    # excursion candle: pushes higher but doesn't close back inside yet
    t += 300
    candles.append(_candle(t, 106, 108, 105.5, 107))
    # fakeout confirmation: closes back below range high
    t += 300
    candles.append(_candle(t, 107, 107, 104, 104))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss == 108.0
    assert sig.take_profit == 104.0 - 2.0 * (108.0 - 104.0)


def test_long_fade_on_fakeout_below_range_low():
    strat = Strategy(reward_risk=2.0)
    candles = _opening_range(high=105.0, low=95.0)
    t = candles[-1]["time"]
    t += 300
    candles.append(_candle(t, 100, 100, 93, 94))
    t += 300
    candles.append(_candle(t, 94, 94.5, 92, 93))
    t += 300
    candles.append(_candle(t, 93, 96, 93, 96))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss == 92.0
    assert sig.take_profit == 96.0 + 2.0 * (96.0 - 92.0)


def test_no_signal_without_fresh_fakeout():
    strat = Strategy()
    candles = _opening_range(high=105.0, low=95.0)
    t = candles[-1]["time"] + 300
    candles.append(_candle(t, 100, 101, 99, 100))
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD


def test_hold_in_position():
    strat = Strategy()
    candles = _opening_range()
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD
