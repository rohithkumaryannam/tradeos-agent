"""Functional tests for the "mastermind" NY-session opening-range breakout
strategy — see strategies/mastermind.py's module docstring for the spec and
the judgment calls made on its few ambiguous points."""
from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.base import Action
from strategies.mastermind import Strategy

NY = ZoneInfo("America/New_York")
STEP = 300  # 5 minutes, in seconds


def _c(t, o, h, l, cl, vol=10):
    return {"time": t, "open": o, "high": h, "low": l, "close": cl, "volume": vol}


def _range_start_ts():
    # A plain winter date (no DST ambiguity) at exactly midnight NY.
    return datetime(2024, 1, 10, 0, 0, tzinfo=NY).timestamp()


def _build_range(flat_price=100.0, range_high=102.0, range_low=98.0):
    """48 five-minute candles spanning the 00:00-04:00 NY window, plus one
    extra candle (at 04:00 NY) to close it. Returns (candles, next_ts) where
    next_ts is the timestamp of the first candle AFTER the closed range."""
    t = _range_start_ts()
    candles = []
    for i in range(48):
        h = range_high if i == 10 else flat_price + 0.5
        l = range_low if i == 20 else flat_price - 0.5
        candles.append(_c(t, flat_price, h, l, flat_price))
        t += STEP
    # one candle at 04:00 NY closes the window (outside the 0-4 hour range)
    candles.append(_c(t, flat_price, flat_price + 0.5, flat_price - 0.5, flat_price))
    t += STEP
    return candles, t


def _strat():
    s = Strategy()
    s.required_candles = 10
    return s


def test_enters_long_on_confirmed_close_above_range_high():
    candles, t = _build_range()
    candles.append(_c(t, 102.0, 103.5, 101.8, 103.0))  # closes above 102 high
    sig = _strat().generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss == 98.0  # opposite edge, not a buffer beyond it
    assert sig.take_profit == 103.0 + (103.0 - 98.0) * 2.0


def test_enters_short_on_confirmed_close_below_range_low():
    candles, t = _build_range()
    candles.append(_c(t, 98.0, 98.2, 96.5, 97.0))  # closes below 98 low
    sig = _strat().generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss == 102.0
    assert sig.take_profit == 97.0 - (102.0 - 97.0) * 2.0


def test_a_wick_beyond_the_range_without_a_close_does_not_enter():
    candles, t = _build_range()
    # high pokes above the range, but the candle CLOSES back inside
    candles.append(_c(t, 100.0, 103.0, 99.5, 100.5))
    sig = _strat().generate(candles, None)
    assert sig.action == Action.HOLD


def test_holds_while_in_position_and_price_stays_outside_the_range():
    candles, t = _build_range()
    candles.append(_c(t, 102.0, 103.5, 101.8, 103.0))
    sig = _strat().generate(candles, "long")
    assert sig.action == Action.HOLD


def test_exits_on_close_back_inside_the_range_invalidation():
    candles, t = _build_range()
    candles.append(_c(t, 102.0, 103.5, 101.8, 103.0))  # breakout candle (already entered)
    t += STEP
    candles.append(_c(t, 103.0, 103.2, 99.5, 100.0))  # closes back inside [98,102]
    sig = _strat().generate(candles, "long")
    assert sig.action == Action.EXIT
    assert sig.exit_with_bracket is True


def test_rearms_and_flips_direction_after_invalidation():
    candles, t = _build_range()
    candles.append(_c(t, 102.0, 103.5, 101.8, 103.0))  # 1: breaks up
    t += STEP
    candles.append(_c(t, 103.0, 103.2, 99.5, 100.0))    # 2: closes back inside -> invalidated
    t += STEP
    candles.append(_c(t, 100.0, 100.2, 96.5, 97.0))     # 3: now breaks DOWN
    sig = _strat().generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss == 102.0


def test_does_not_rearm_without_closing_back_inside_first():
    candles, t = _build_range()
    candles.append(_c(t, 102.0, 103.5, 101.8, 103.0))  # breaks up, already "entered" conceptually
    t += STEP
    candles.append(_c(t, 103.0, 104.0, 102.5, 103.8))  # keeps closing outside, never re-entered range
    sig = _strat().generate(candles, None)
    assert sig.action == Action.HOLD


def test_holds_before_any_complete_range_is_in_view():
    t = _range_start_ts()
    candles = [_c(t + i * STEP, 100, 100.5, 99.5, 100) for i in range(20)]  # mid-window, not yet closed
    sig = _strat().generate(candles, None)
    assert sig.action == Action.HOLD
    assert "waiting for the first complete" in sig.reason


def test_holds_while_warming_up():
    candles = [_c(_range_start_ts(), 100, 101, 99, 100)]
    assert Strategy().generate(candles, None).action == Action.HOLD
