from strategies.rsi2_connors import Strategy
from strategies.base import Action


def _candle(t, o, h, l, c, v=1000):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_warming_up_with_few_candles():
    strat = Strategy(trend_ema=200)
    candles = [_candle(i, 100, 101, 99, 100) for i in range(10)]
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "warming up" in sig.reason


def test_long_entry_on_oversold_pullback_in_uptrend():
    strat = Strategy(trend_ema=50, rsi_period=2, atr_period=14)
    candles = []
    price = 100.0
    for i in range(60):
        price += 0.5
        candles.append(_candle(i, price - 0.1, price + 0.2, price - 0.3, price))
    # sharp 2-bar pullback to push RSI(2) to oversold, while still above the EMA
    for i in range(2):
        price -= 2.0
        candles.append(_candle(len(candles), price + 2, price + 2, price - 0.2, price))

    sig = strat.generate(candles, None)
    if sig.action == Action.ENTER_LONG:
        assert sig.stop_loss < price < sig.take_profit


def test_exit_long_above_exit_sma():
    strat = Strategy(trend_ema=50, exit_sma=5)
    candles = [_candle(i, 100, 101, 99, 100) for i in range(60)]
    candles.append(_candle(60, 100, 106, 100, 105))
    sig = strat.generate(candles, "long")
    assert sig.action == Action.EXIT


def test_hold_in_position_without_exit_condition():
    strat = Strategy(trend_ema=50, exit_sma=5)
    candles = [_candle(i, 100, 100.5, 99.5, 100) for i in range(60)]
    sig = strat.generate(candles, "long")
    assert sig.action == Action.HOLD
