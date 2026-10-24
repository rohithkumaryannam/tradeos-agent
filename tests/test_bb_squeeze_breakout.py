from strategies.bb_squeeze_breakout import Strategy
from strategies.base import Action


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_warming_up_with_few_candles():
    strat = Strategy()
    candles = [_candle(i, 100, 101, 99, 100) for i in range(10)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "warming up" in sig.reason


def test_long_entry_on_squeeze_breakout_with_volume():
    strat = Strategy(bb_period=20, squeeze_lookback=100)
    candles = []
    price = 100.0
    # long flat/tight range to build a volatility squeeze
    for i in range(130):
        candles.append(_candle(i, price, price + 0.05, price - 0.05, price, 1000))
    # breakout candle: big move up, strong volume
    breakout_price = price + 5
    candles.append(_candle(len(candles), price, breakout_price, price, breakout_price, 5000))

    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < breakout_price < sig.take_profit


def test_hold_when_in_position():
    strat = Strategy()
    candles = [_candle(i, 100, 100.5, 99.5, 100, 1000) for i in range(130)]
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD


def test_no_entry_without_squeeze():
    strat = Strategy(bb_period=20, squeeze_lookback=100)
    candles = []
    price = 100.0
    # choppy/wide range candles -> no squeeze
    for i in range(130):
        offset = 3 if i % 2 == 0 else -3
        candles.append(_candle(i, price, price + abs(offset) + 1, price - abs(offset) - 1, price + offset, 1000))
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
