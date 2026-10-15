"""NY Range Fakeout Reversal — a liquidity-sweep / reversal strategy.

It trades the trap, not the trend:

1. Range: the high/low of the first 4-hour NY-session window each day
   (00:00-04:00 America/New_York), measured from the 5m candles in that window.
2. After the window, watch for a 5m close beyond the range (a breakout) that
   immediately fails — the very next 5m candle closes back inside the range.
3. Fade the trap:
     SHORT if a candle closes above the range high, the next closes back below it.
     LONG  if a candle closes below the range low,  the next closes back above it.
4. Risk: stop at the failed-breakout candle's extreme (its high for a short, its
   low for a long); target at `rr`x that risk distance (default 2:1). The stop and
   target are carried on the Signal, so the risk engine sizes the position off this
   structural stop rather than the config % bracket.

Designed for 5m candles. One setup per NY day. Needs candle 'time' (unix seconds).
Select it in config.yaml:
    strategy: { name: ny_range_fakeout, params: { rr: 2.0 } }
"""
from __future__ import annotations
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tz database is unavailable
    _NY = timezone.utc

from strategies.base import BaseStrategy, Signal, Action

name = "ny_range_fakeout"


def _ny(ts) -> datetime:
    return datetime.fromtimestamp(int(ts), _NY)


class Strategy(BaseStrategy):
    def __init__(self, rr=2.0, range_start_hour=0, range_end_hour=4, **kw):
        super().__init__(rr=rr, range_start_hour=range_start_hour,
                         range_end_hour=range_end_hour, **kw)
        self.rr = float(rr)
        self.h0 = int(range_start_hour)
        self.h1 = int(range_end_hour)
        self._last_day = None   # NY date we last took a setup on (one per day)

    def _day_range(self, candles, day):
        his, los = [], []
        for c in candles:
            dt = _ny(c["time"])
            if dt.date() == day and self.h0 <= dt.hour < self.h1:
                his.append(float(c["high"]))
                los.append(float(c["low"]))
        return (max(his), min(los)) if his else None

    def generate(self, candles, position_side):
        if position_side is not None:
            return Signal(Action.HOLD, "in position")  # exits ride the SL/TP
        if len(candles) < 3 or "time" not in candles[-1]:
            return Signal(Action.HOLD, "warming up")

        curr, prev = candles[-1], candles[-2]
        cdt = _ny(curr["time"])
        day = cdt.date()

        if self.h0 <= cdt.hour < self.h1:
            return Signal(Action.HOLD, "building NY range")

        rng = self._day_range(candles, day)
        if rng is None:
            return Signal(Action.HOLD, "no NY range for today")
        hi, lo = rng

        if self._last_day == day:
            return Signal(Action.HOLD, "already traded today")

        pdt = _ny(prev["time"])
        if not (pdt.date() == day and pdt.hour >= self.h1):
            return Signal(Action.HOLD, "waiting post-range")

        pc, cc = float(prev["close"]), float(curr["close"])
        ph, pl = float(prev["high"]), float(prev["low"])

        # SHORT: broke above the high, reclaimed back below it
        if pc > hi and cc < hi:
            entry, sl = cc, ph
            risk = sl - entry
            if risk > 0:
                tp = entry - self.rr * risk
                self._last_day = day
                return Signal(Action.ENTER_SHORT,
                              f"NY fakeout short: swept {hi:.0f}, reclaimed; "
                              f"SL {sl:.0f} TP {tp:.0f} ({self.rr:g}R)",
                              stop_loss=sl, take_profit=tp)

        # LONG: broke below the low, reclaimed back above it
        if pc < lo and cc > lo:
            entry, sl = cc, pl
            risk = entry - sl
            if risk > 0:
                tp = entry + self.rr * risk
                self._last_day = day
                return Signal(Action.ENTER_LONG,
                              f"NY fakeout long: swept {lo:.0f}, reclaimed; "
                              f"SL {sl:.0f} TP {tp:.0f} ({self.rr:g}R)",
                              stop_loss=sl, take_profit=tp)

        return Signal(Action.HOLD, "no fakeout setup")
