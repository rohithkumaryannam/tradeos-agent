"""Combo meta-strategy: it should take the first sub-strategy's entry and tag the
signal with that strategy's name."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategies.combo import Strategy
from strategies.base import Action

NY = ZoneInfo("America/New_York")


def _ts(dt):
    return int(dt.timestamp())


def _ny_fakeout_short_candles():
    day = datetime(2024, 1, 15, tzinfo=NY).date()
    hi, lo = 100_100, 99_900
    out, t = [], datetime(day.year, day.month, day.day, 0, 0, tzinfo=NY)
    end = t + timedelta(hours=4)
    mid = (hi + lo) / 2
    while t < end:
        out.append({"time": _ts(t), "open": mid, "high": hi - 5, "low": lo + 5,
                    "close": mid, "volume": 10})
        t += timedelta(minutes=5)
    out[1]["high"] = hi
    out[2]["low"] = lo
    out.append({"time": _ts(end), "open": 100_120, "high": 100_200,
                "low": 100_120, "close": 100_150, "volume": 10})          # breakout
    out.append({"time": _ts(end + timedelta(minutes=5)), "open": 100_150,
                "high": 100_160, "low": 100_040, "close": 100_050, "volume": 10})  # reclaim
    return out


def test_combo_tags_the_owning_strategy():
    candles = _ny_fakeout_short_candles()
    sig = Strategy(subs=["ny_range_fakeout", "ema_crossover"]).generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.strategy == "ny_range_fakeout"
    assert sig.reason.startswith("[ny_range_fakeout]")
    assert sig.stop_loss == 100_200          # structural SL carried through


def test_default_combo_includes_every_live_strategy():
    assert [name for name, _ in Strategy().subs] == [
        "ny_range_fakeout", "ema_crossover",
        "donchian_breakout", "time_series_momentum",
    ]
