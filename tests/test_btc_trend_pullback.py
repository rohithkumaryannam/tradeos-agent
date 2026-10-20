"""Functional tests for btc_trend_pullback — a live port of the precise
rules in strategies/btc_trend_pullback_backtest.py. Every assertion here
mirrors an explicit, unambiguous condition from that script (unlike the
price-action/SMC-style strategies, nothing here needed a judgment call)."""
import pytest

from strategies.btc_trend_pullback import Strategy
from strategies.base import Action


def _c(o, h, l, cl, t):
    return {"time": t * 900, "open": o, "high": h, "low": l, "close": cl, "volume": 10}


def _uptrend_candles(n=240, step=2.0, start=1000.0):
    candles = []
    p = start
    t = 0
    for _ in range(n):
        p += step
        candles.append(_c(p - step, p + 1, p - step - 1, p, t))
        t += 1
    return candles, p, t


def _downtrend_candles(n=240, step=2.0, start=2000.0):
    candles = []
    p = start
    t = 0
    for _ in range(n):
        p -= step
        candles.append(_c(p + step, p + step + 1, p - 1, p, t))
        t += 1
    return candles, p, t


def test_uptrend_pullback_with_bullish_confirmation_enters_long():
    candles, p, t = _uptrend_candles()
    # pullback: dips down to the EMA20 area, closes a bit above it
    candles.append(_c(1480, 1480.5, 1460, 1470, t)); t += 1
    # confirmation: bullish, closes back above EMA20
    candles.append(_c(1470, 1485.5, 1469.5, 1485, t)); t += 1

    sig = Strategy().generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < candles[-1]["close"] < sig.take_profit
    # 2R by default: reward should be ~2x the risk
    risk = candles[-1]["close"] - sig.stop_loss
    reward = sig.take_profit - candles[-1]["close"]
    assert reward == pytest.approx(2 * risk, rel=1e-6)


def test_downtrend_pullback_with_bearish_confirmation_enters_short():
    candles, p, t = _downtrend_candles()
    candles.append(_c(p, p + 20, p - 0.5, p + 10, t)); t += 1
    candles.append(_c(p + 10, p + 10.5, p - 5, p - 5, t)); t += 1

    sig = Strategy().generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss > candles[-1]["close"] > sig.take_profit


def test_time_stop_exits_after_max_holding_bars():
    candles, p, t = _uptrend_candles()
    candles.append(_c(1480, 1480.5, 1460, 1470, t)); t += 1
    candles.append(_c(1470, 1485.5, 1469.5, 1485, t)); t += 1

    strat = Strategy(max_holding_bars=5)
    entry = strat.generate(candles, None)
    assert entry.action == Action.ENTER_LONG

    sig = None
    for _ in range(6):
        p = float(candles[-1]["close"]) + 0.1
        candles.append(_c(p - 0.1, p + 0.2, p - 0.2, p, t)); t += 1
        sig = strat.generate(candles, "long")
        if sig.action == Action.EXIT:
            break
    assert sig.action == Action.EXIT
    assert "time-stop" in sig.reason
    assert sig.exit_with_bracket is True


def test_holds_with_no_pullback_in_a_clean_uptrend():
    candles, _, _ = _uptrend_candles()
    sig = Strategy().generate(candles, None)
    assert sig.action == Action.HOLD


def test_holds_without_a_clear_trend():
    candles = []
    p = 1000.0
    for i in range(240):
        p += 1 if i % 2 == 0 else -1
        candles.append(_c(p, p + 2, p - 2, p, i))
    sig = Strategy().generate(candles, None)
    assert sig.action == Action.HOLD


def test_holds_while_warming_up():
    candles = [_c(1000, 1001, 999, 1000, i) for i in range(10)]
    assert Strategy().generate(candles, None).action == Action.HOLD
