from strategies.base import Action
from strategies.time_series_momentum import Strategy


def _candles(step, count=40):
    prices = [100.0 + step * i for i in range(count)]
    return [
        {
            "time": i * 3600,
            "open": price - step,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 10,
        }
        for i, price in enumerate(prices)
    ]


def _strategy():
    return Strategy(
        horizons=(3, 5, 10), trend_period=20, atr_period=3,
        min_move_atr=0.25, stop_atr=2.0, reward_r=3.0,
    )


def test_aligned_positive_momentum_enters_long():
    sig = _strategy().generate(_candles(2.0), None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < sig.take_profit


def test_aligned_negative_momentum_enters_short():
    sig = _strategy().generate(_candles(-2.0), None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss > sig.take_profit


def test_short_horizon_reversal_exits_owned_position():
    candles = _candles(2.0)
    last = candles[-1]["close"]
    candles.append({
        "time": len(candles) * 3600,
        "open": last,
        "high": last + 1,
        "low": last - 8,
        "close": last - 7,
        "volume": 10,
    })
    sig = _strategy().generate(candles, "long")
    assert sig.action == Action.EXIT
    assert sig.exit_with_bracket is True
