from strategies.darkknight_sol import Strategy
from strategies.base import Action


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _warm_uptrend(n=210, start=100.0, step=0.3):
    """n candles in a steady uptrend so EMA50 > EMA200, flat before the
    breakout candle itself."""
    candles = []
    price = start
    for i in range(n):
        price += step
        candles.append(_candle(i, price - 0.1, price + 0.1, price - 0.2, price, 1000))
    return candles, price


def test_long_entry_on_breakout_with_trend_momentum_and_volume():
    strat = Strategy()
    candles, last_price = _warm_uptrend()
    # hold the price flat for sr_period candles so the breakout candle
    # clears the rolling high
    flat = last_price
    for i in range(20):
        candles.append(_candle(len(candles), flat, flat + 0.05, flat - 0.05, flat, 1000))
    # breakout candle: new high, strong volume
    breakout_price = flat + 5
    candles.append(_candle(len(candles), flat, breakout_price, flat, breakout_price, 5000))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < breakout_price < sig.take_profit


def test_hold_when_in_position():
    strat = Strategy()
    candles, _ = _warm_uptrend()
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD


def test_hold_without_enough_candles():
    strat = Strategy()
    candles = [_candle(i, 100, 101, 99, 100) for i in range(10)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "warming up" in sig.reason


def test_no_entry_without_volume_confirmation():
    strat = Strategy()
    candles, last_price = _warm_uptrend()
    flat = last_price
    for i in range(20):
        candles.append(_candle(len(candles), flat, flat + 0.05, flat - 0.05, flat, 1000))
    # breakout candle with weak volume (no confirmation)
    breakout_price = flat + 5
    candles.append(_candle(len(candles), flat, breakout_price, flat, breakout_price, 1000))

    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
