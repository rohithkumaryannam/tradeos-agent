"""Functional tests for the live mean-reversion strategy."""
from strategies.base import Action
from strategies.mean_reversion import Strategy


def _candle(price, i):
    return {
        "time": i * 900,
        "open": price,
        "high": price + 0.5,
        "low": price - 0.5,
        "close": price,
        "volume": 10,
    }


def _flat_then(price):
    values = [100.0] * 40 + [price]
    return [_candle(value, i) for i, value in enumerate(values)]


def test_enters_long_at_oversold_lower_band_extreme():
    sig = Strategy(adx_max=101).generate(_flat_then(90.0), None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < 90.0 < sig.take_profit


def test_enters_short_at_overbought_upper_band_extreme():
    sig = Strategy(adx_max=101).generate(_flat_then(110.0), None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.take_profit < 110.0 < sig.stop_loss


def test_adx_regime_filter_blocks_entries():
    sig = Strategy(adx_max=0.01).generate(_flat_then(90.0), None)
    assert sig.action == Action.HOLD
    assert "ADX" in sig.reason


def test_time_stop_exits_after_configured_bars():
    candles = _flat_then(90.0)
    strategy = Strategy(adx_max=101, max_holding_bars=2)
    assert strategy.generate(candles, None).action == Action.ENTER_LONG
    candles.extend([_candle(91.0, 41), _candle(92.0, 42)])
    sig = strategy.generate(candles, "long")
    assert sig.action == Action.EXIT
    assert sig.exit_with_bracket is True
