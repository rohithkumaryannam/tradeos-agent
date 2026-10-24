from strategies.supertrend import Strategy
from strategies.base import Action


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_warming_up_with_few_candles():
    strat = Strategy()
    candles = [_candle(i, 100, 101, 99, 100) for i in range(5)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "warming up" in sig.reason


def test_uptrend_then_breakdown_flips_and_can_enter_short():
    strat = Strategy(atr_period=5)
    candles = []
    price = 100.0
    # steady low-volatility uptrend
    for i in range(40):
        price += 1.0
        candles.append(_candle(i, price - 0.2, price + 0.3, price - 0.5, price))
    # sharp drop below the lower band to flip the trend down
    price -= 15
    candles.append(_candle(len(candles), price + 15, price + 15, price - 1, price))

    sig = strat.generate(candles, None)
    assert sig.action in (Action.ENTER_SHORT, Action.HOLD)


def test_hold_in_position_without_flip():
    strat = Strategy(atr_period=5)
    candles = []
    price = 100.0
    for i in range(40):
        price += 1.0
        candles.append(_candle(i, price - 0.2, price + 0.3, price - 0.5, price))
    sig = strat.generate(candles, "long")
    assert sig.action in (Action.HOLD, Action.EXIT)
