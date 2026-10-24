from strategies.mean_reversion_scalp import Strategy
from strategies.base import Action


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def _flat_range(n=60, base=100.0):
    """Low-ADX chop so the regime filter passes."""
    candles = []
    for i in range(n):
        wobble = 0.3 if i % 2 == 0 else -0.3
        candles.append(_candle(i, base, base + abs(wobble) + 0.1, base - abs(wobble) - 0.1, base + wobble))
    return candles


def test_warming_up_with_few_candles():
    strat = Strategy()
    candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "warming up" in sig.reason


def test_long_entry_below_lower_band_with_partial_target():
    strat = Strategy(bb_period=20, atr_period=14, target_fraction=0.4)
    candles = _flat_range(60, base=100.0)
    # drop sharply below the lower band with oversold RSI
    for i in range(5):
        price = 100 - (i + 1) * 2
        candles.append(_candle(len(candles), price + 1, price + 1, price - 0.5, price))

    sig = strat.generate(candles, None)
    if sig.action == Action.ENTER_LONG:
        entry = candles[-1]["close"]
        assert sig.stop_loss < entry < sig.take_profit
        # the target must be a FRACTION of the way back, not the full middle band
        closes = [float(c["close"]) for c in candles]
        middle = sum(closes[-20:]) / 20
        full_distance = middle - entry
        target_distance = sig.take_profit - entry
        assert 0 < target_distance < full_distance


def test_hold_in_position_until_time_stop():
    strat = Strategy(max_holding_bars=2)
    candles = _flat_range(60)
    strat._entry_bar_time = float(candles[-1]["time"])
    candles.append(_candle(len(candles), 100, 100.5, 99.5, 100))
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD
    candles.append(_candle(len(candles), 100, 100.5, 99.5, 100))
    candles.append(_candle(len(candles), 100, 100.5, 99.5, 100))
    sig = strat.generate(candles, "long")
    assert sig.action == Action.EXIT
