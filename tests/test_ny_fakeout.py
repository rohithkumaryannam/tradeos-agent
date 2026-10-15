"""Tests for the NY Range Fakeout Reversal strategy and the strategy-set SL/TP
path through the risk engine."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategies.ny_range_fakeout import Strategy
from strategies.base import Action
from core.risk import RiskEngine
from core.settings import RiskConfig

NY = ZoneInfo("America/New_York")


def _ts(dt):
    return int(dt.timestamp())


def _range_candles(day, hi=100_100, lo=99_900):
    """5m candles across the 00:00-04:00 NY window, bounded by hi/lo."""
    out = []
    t = datetime(day.year, day.month, day.day, 0, 0, tzinfo=NY)
    end = t + timedelta(hours=4)
    mid = (hi + lo) / 2
    while t < end:
        out.append({"time": _ts(t), "open": mid, "high": hi - 5,
                    "low": lo + 5, "close": mid, "volume": 10})
        t += timedelta(minutes=5)
    # pin the exact range extremes on two of the bars
    out[1]["high"] = hi
    out[2]["low"] = lo
    return out, end


def _bar(dt, o, h, l, c):
    return {"time": _ts(dt), "open": o, "high": h, "low": l, "close": c, "volume": 10}


def test_short_fakeout_triggers_with_structural_sltp():
    day = datetime(2024, 1, 15, tzinfo=NY).date()
    candles, end = _range_candles(day)          # range hi=100100 lo=99900
    # breakout candle closes ABOVE the high, next closes back BELOW it
    breakout = _bar(end, 100_120, 100_200, 100_120, 100_150)
    reversal = _bar(end + timedelta(minutes=5), 100_150, 100_160, 100_040, 100_050)
    candles += [breakout, reversal]

    sig = Strategy(rr=2.0).generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss == 100_200                 # failed-breakout candle high
    # entry 100050, risk 150, 2R target = 100050 - 300
    assert sig.take_profit == 99_750


def test_long_fakeout_triggers():
    day = datetime(2024, 1, 15, tzinfo=NY).date()
    candles, end = _range_candles(day)
    breakout = _bar(end, 99_880, 99_880, 99_800, 99_850)      # closes below low
    reversal = _bar(end + timedelta(minutes=5), 99_850, 99_960, 99_840, 99_950)  # back above
    candles += [breakout, reversal]

    sig = Strategy(rr=2.0).generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss == 99_800                  # failed-breakout candle low
    assert sig.take_profit == 99_950 + 2 * (99_950 - 99_800)


def test_no_trade_while_building_range():
    day = datetime(2024, 1, 15, tzinfo=NY).date()
    candles, _ = _range_candles(day)
    assert Strategy().generate(candles, None).action == Action.HOLD


def test_one_setup_per_day():
    day = datetime(2024, 1, 15, tzinfo=NY).date()
    candles, end = _range_candles(day)
    candles += [_bar(end, 100_120, 100_200, 100_120, 100_150),
                _bar(end + timedelta(minutes=5), 100_150, 100_160, 100_040, 100_050)]
    s = Strategy()
    assert s.generate(candles, None).action == Action.ENTER_SHORT
    # a second identical setup later the same day must not re-fire
    candles += [_bar(end + timedelta(minutes=10), 100_120, 100_200, 100_120, 100_150),
                _bar(end + timedelta(minutes=15), 100_150, 100_160, 100_040, 100_050)]
    assert s.generate(candles, None).action == Action.HOLD


def test_risk_engine_honors_strategy_sltp():
    cfg = RiskConfig(account_equity=10_000, risk_per_trade_pct=1.0, max_leverage=50)
    r = RiskEngine(cfg)
    plan = r.size_position("short", 100_050, contract_value=0.001 * 100_050,
                           stop_loss=100_200, take_profit=99_750)
    assert plan.ok
    assert plan.stop_loss == 100_200 and plan.take_profit == 99_750
