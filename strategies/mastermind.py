"""Mastermind — NY-session opening-range breakout, with a structural
invalidation that re-arms for the opposite direction instead of just eating
the stop. Designed by the user from a live BTC trade (chart with BOS/CHoCH
structure markup) and refined through discussion before being built.

Rules, as specified:
  1. Reference range = the 12:00 AM - 4:00 AM New York candle (a 4-hour
     window), recorded once it closes. Re-derived from NY-local wall-clock
     time via the system timezone database (not a fixed UTC offset), so it
     stays correct across New York's DST transitions automatically.
  2. Breakout confirmation = a 5-minute candle's CLOSE beyond the range's
     high or low — a wick alone doesn't count. This is a deliberate filter
     against exactly the false-breakout problem that's been hurting
     donchian_breakout on BTC live (see config.yaml's notes on donchian_btc).
  3. Entry: on a confirmed breakout, enter in that direction. Stop-loss =
     the range's OPPOSITE edge (not a buffer beyond it) — the same
     structural level that defined the setup is what invalidates it.
     Take-profit = 1:2 reward:risk from entry, off that stop distance.
  4. Invalidation: if a later candle closes back INSIDE the range while in
     the trade, exit immediately — don't wait for the hard stop. The
     re-entry into the range is itself the signal the breakout failed.
  5. Re-arm: after an invalidation (or after being stopped out), the next
     confirmed close beyond EITHER edge is a fresh, valid entry — same
     direction (confirms the original move was just shaken out, not
     reversed) or the opposite direction (the breakout was a fakeout and
     the real move is the other way). No functional difference between
     these two cases; "which one happens" is just how it reads afterward.
  6. Re-entries are uncapped for the rest of that range's trading day
     (until the next day's NY range closes and supersedes it).
  7. Entries are evaluated on 5-minute candles.

Judgment calls made where the spec left a choice open:
  - "SL should be in the range" -> read as exactly the range's opposite
    edge, not the edge plus a buffer, nor some point inside the range body.
  - The invalidation close-back-inside and the breakout close-outside use
    the SAME confirmation rule (a candle's close, not its wick) for
    consistency, though only the breakout rule was stated explicitly.
  - "Re-arming" requires having been back inside the range (or never having
    broken out yet) before a new breakout counts as fresh — this is what
    makes the cascade meaningful; without it, a stopped-out position in a
    strong trend would just re-enter on literally the next candle without
    ever confirming price actually came back.
  - No cap was given on same-day re-entries, so none is enforced here
    beyond the range itself resetting once the next day's NY window closes.
"""
from __future__ import annotations

from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - missing system tzdata is unusual but not impossible
    from datetime import timezone, timedelta
    _NY = timezone(timedelta(hours=-5))  # fixed EST fallback; no DST handling

from strategies.base import BaseStrategy, Signal, Action

name = "mastermind"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "5m"
    supported_resolutions = ("5m",)

    def __init__(self, range_start_hour=0, range_end_hour=4, reward_risk=2.0, **kw):
        super().__init__(range_start_hour=range_start_hour, range_end_hour=range_end_hour,
                          reward_risk=reward_risk, **kw)
        self.range_start_hour = range_start_hour
        self.range_end_hour = range_end_hour
        self.reward_risk = reward_risk
        # The oldest point we might ever need is "just before today's NY
        # window closes" -> the most recent complete range is YESTERDAY's,
        # up to ~24h + range length ago. ~33h of 5m candles covers that with
        # a safety margin for any gaps in the feed.
        self.required_candles = 400

    def _latest_closed_range(self, candles):
        """The most recently COMPLETED NY reference window's (high, low,
        end_index) - end_index is the index of the first candle AFTER the
        window, so candles[end_index:] is "since the range closed". None if
        no complete window is in view yet."""
        cur_hi = cur_lo = None
        closed = None
        for i, c in enumerate(candles):
            t = datetime.fromtimestamp(float(c["time"]), tz=_NY)
            in_window = self.range_start_hour <= t.hour < self.range_end_hour
            if in_window:
                h, l = float(c["high"]), float(c["low"])
                cur_hi = h if cur_hi is None else max(cur_hi, h)
                cur_lo = l if cur_lo is None else min(cur_lo, l)
            elif cur_hi is not None:
                closed = (cur_hi, cur_lo, i)
                cur_hi = cur_lo = None
        return closed

    def _armed(self, after_candles, range_hi, range_lo):
        """Scan every candle since the range closed EXCEPT the latest one:
        True if the sequence of closes-outside/closes-inside leaves us
        eligible for a fresh breakout entry right now (see rule 5 above)."""
        armed = True
        for c in after_candles[:-1]:
            cl = float(c["close"])
            if armed:
                if cl > range_hi or cl < range_lo:
                    armed = False
            elif range_lo <= cl <= range_hi:
                armed = True
        return armed

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        rng = self._latest_closed_range(candles)
        if rng is None:
            return Signal(Action.HOLD, "waiting for the first complete NY range")
        range_hi, range_lo, end_index = rng
        after = candles[end_index:]
        if not after:
            return Signal(Action.HOLD, "range just closed; no candles after it yet")

        price = float(candles[-1]["close"])

        if position_side is not None:
            if range_lo <= price <= range_hi:
                return Signal(Action.EXIT,
                              "closed back inside the NY range; breakout invalidated",
                              exit_with_bracket=True)
            return Signal(Action.HOLD, "in position")

        if not self._armed(after, range_hi, range_lo):
            return Signal(Action.HOLD, "already broke out; waiting for a close back "
                                        "inside the range before arming again")

        if price > range_hi:
            sl = range_lo
            risk = price - sl
            if risk <= 0:
                return Signal(Action.HOLD, "range too narrow for a valid stop")
            tp = price + risk * self.reward_risk
            return Signal(Action.ENTER_LONG,
                          "confirmed close above the NY range high",
                          stop_loss=sl, take_profit=tp)

        if price < range_lo:
            sl = range_hi
            risk = sl - price
            if risk <= 0:
                return Signal(Action.HOLD, "range too narrow for a valid stop")
            tp = price - risk * self.reward_risk
            return Signal(Action.ENTER_SHORT,
                          "confirmed close below the NY range low",
                          stop_loss=sl, take_profit=tp)

        return Signal(Action.HOLD, "inside the NY range")
