"""NY 4-hour opening range breakout (ORB) — exact rules as specified, no
judgment calls needed since the rules were given precisely:

  1. The day's range = the high/low of the first 4-hour window of the day
     in New York time (00:00-04:00 America/New_York), extended for the
     rest of that NY calendar day.
  2. Entries are taken on 5-minute candles: wait for a candle to CLOSE
     fully outside the range.
  3. Long on a close above the range high; short on a close below the
     range low. Stop-loss at the broken level (range high for longs,
     range low for shorts — that level, now broken, is the invalidation
     point). Take-profit at >= 2:1 reward:risk from that stop distance.

This strategy is fed 5-minute candles only (closed_candles_only); the NY
4H range is derived by grouping those same 5m candles into their NY
calendar day and 00:00-04:00 window, rather than needing a second,
independently-fetched 4h feed.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

from strategies.base import BaseStrategy, Signal, Action

name = "ny_4h_orb"

NY = ZoneInfo("America/New_York")


def _ny_day_and_hour(epoch_seconds):
    dt = datetime.fromtimestamp(epoch_seconds, tz=NY)
    return dt.date(), dt.hour


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "5m"
    supported_resolutions = ("5m",)

    def __init__(self, reward_risk=2.0, **kw):
        super().__init__(reward_risk=reward_risk, **kw)
        self.reward_risk = float(reward_risk)
        self.required_candles = 400  # comfortably > 1 NY day of 5m bars (288/day)

    def _range_for_today(self, candles):
        """(range_high, range_low, range_complete) for the NY calendar day
        of the LAST candle. range_complete is False while the 00:00-04:00
        window itself is still forming (no range to trade yet)."""
        last_day, last_hour = _ny_day_and_hour(float(candles[-1]["time"]))
        window_highs, window_lows = [], []
        for c in candles:
            day, hour = _ny_day_and_hour(float(c["time"]))
            if day == last_day and hour < 4:
                window_highs.append(float(c["high"]))
                window_lows.append(float(c["low"]))
        if not window_highs:
            return None, None, False
        range_complete = last_hour >= 4
        return max(window_highs), min(window_lows), range_complete

    def generate(self, candles, position_side):
        if len(candles) < 10:
            return Signal(Action.HOLD, "warming up")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        range_high, range_low, range_complete = self._range_for_today(candles)
        if range_high is None:
            return Signal(Action.HOLD, "warming up")
        if not range_complete:
            return Signal(Action.HOLD, "NY opening range still forming")

        price = float(candles[-1]["close"])

        if price > range_high:
            risk = price - range_high
            if risk <= 0:
                return Signal(Action.HOLD, "invalid range")
            tp = price + self.reward_risk * risk
            return Signal(Action.ENTER_LONG,
                          f"closed above NY 4H opening range high ({range_high:.4f})",
                          stop_loss=range_high, take_profit=tp)
        if price < range_low:
            risk = range_low - price
            if risk <= 0:
                return Signal(Action.HOLD, "invalid range")
            tp = price - self.reward_risk * risk
            return Signal(Action.ENTER_SHORT,
                          f"closed below NY 4H opening range low ({range_low:.4f})",
                          stop_loss=range_low, take_profit=tp)
        return Signal(Action.HOLD, "inside the NY 4H opening range")
