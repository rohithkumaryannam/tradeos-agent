""""BTC Brahmastra" / "BTC BMS" — a price-action zone-rejection scalp,
ported from a narrated strategy description (no Pine source was available),
so several specifics below are judgment calls, flagged where made. Runs on
5m candles per the original description.

Rules, as given:
  1. Identify the trend.
  2. Mark a zone: three consecutive same-color candles (green = buy zone,
     red = sell zone).
  3. Entry: price returns to the zone and forms a rejection candle; enter
     on the next candle's close.
  4. Tight stop-loss, let winners run (1:5 to 1:10 R, not a fixed rule).

Judgment calls made (none of these were specified precisely):
  - "Identify the trend" -> market structure: the last two confirmed swing
    highs and swing lows. Higher-high + higher-low = uptrend (only buy
    zones are valid); lower-high + lower-low = downtrend (only sell zones
    valid). No clear HH/HL or LH/LL sequence -> no trade.
  - A swing point is a `swing_lookback`-bar fractal (default 2): a high
    that's the highest of the `lookback` candles on each side of it, same
    for a low. This needs `lookback` candles AFTER a point to confirm it,
    so swing detection lags by that many candles — normal for structure-
    based methods, but means the trend read is never fully "live."
  - "Rejection candle" -> a full-body engulfing candle (its open/close body
    engulfs the prior candle's open/close body), closing in the trade
    direction, with the engulfing candle's range overlapping the zone.
  - "Returns to this zone" -> requires the zone to have actually been left
    first (at least one candle fully outside its range) before price comes
    back — otherwise every candle right after the zone forms would trivially
    "return" to it.
  - Stop-loss = the zone's far edge, with a small buffer (`sl_buffer_pct`,
    default 0.1% of price) beyond it.
  - Take-profit: the description gives no fixed rule ("let it run"), but the
    risk engine requires a bracket, so take_profit is set far away
    (`fallback_tp_mult` x stop distance, default 10 — the upper end of the
    quoted 1:5-1:10) purely as a safety cap that should rarely be hit. The
    REAL exit is a structure break against the trade: a new swing low below
    the prior one (long) or a new swing high above the prior one (short).
"""
from __future__ import annotations

from strategies.base import BaseStrategy, Signal, Action

name = "btc_bms"


def _body(c):
    o, cl = float(c["open"]), float(c["close"])
    return (min(o, cl), max(o, cl))


def _is_bull(c):
    return float(c["close"]) > float(c["open"])


def _is_bear(c):
    return float(c["close"]) < float(c["open"])


def _engulfs(prev, cur):
    """True if cur's open/close body fully contains prev's."""
    pl, ph = _body(prev)
    cl, ch = _body(cur)
    return cl <= pl and ch >= ph


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "5m"
    supported_resolutions = ("5m",)

    def __init__(self, swing_lookback=2, sl_buffer_pct=0.1, fallback_tp_mult=10.0, **kw):
        super().__init__(swing_lookback=swing_lookback, sl_buffer_pct=sl_buffer_pct,
                          fallback_tp_mult=fallback_tp_mult, **kw)
        self.swing_lookback = swing_lookback
        self.sl_buffer_pct = sl_buffer_pct
        self.fallback_tp_mult = fallback_tp_mult
        # enough history for several confirmed swings either side, plus the
        # zone-then-return sequence itself
        self.required_candles = 60

    def _swings(self, candles):
        k = self.swing_lookback
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        swing_highs, swing_lows = [], []
        for i in range(k, len(candles) - k):
            window_h = highs[i - k:i] + highs[i + 1:i + k + 1]
            if highs[i] > max(window_h):
                swing_highs.append((i, highs[i]))
            window_l = lows[i - k:i] + lows[i + 1:i + k + 1]
            if lows[i] < min(window_l):
                swing_lows.append((i, lows[i]))
        return swing_highs, swing_lows

    def _trend(self, swing_highs, swing_lows):
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return None
        hh = swing_highs[-1][1] > swing_highs[-2][1]
        hl = swing_lows[-1][1] > swing_lows[-2][1]
        lh = swing_highs[-1][1] < swing_highs[-2][1]
        ll = swing_lows[-1][1] < swing_lows[-2][1]
        if hh and hl:
            return "up"
        if lh and ll:
            return "down"
        return None

    def _find_zone(self, candles, color_check, before_index):
        """The most recent *exactly-3-candle* zone (matching color_check)
        ending strictly before before_index: the moment a same-color streak
        FIRST reaches 3 candles. A 4th+ candle continuing that same color is
        just the ensuing move, not more zone — "mark the zone" means a fixed
        3-candle box, not an ever-expanding run. Returns
        (zone_low, zone_high, zone_end_index) or None."""
        i = before_index - 1
        while i >= 2:
            is_completion = (
                color_check(candles[i]) and color_check(candles[i - 1]) and color_check(candles[i - 2])
                and not (i - 3 >= 0 and color_check(candles[i - 3]))
            )
            if is_completion:
                lows = [float(c["low"]) for c in candles[i - 2:i + 1]]
                highs = [float(c["high"]) for c in candles[i - 2:i + 1]]
                return min(lows), max(highs), i
            i -= 1
        return None

    def _left_and_returned(self, candles, zone_low, zone_high, zone_end, cur_index):
        """True if some candle between the zone and cur_index closed fully
        outside [zone_low, zone_high] (a genuine departure), so cur_index
        touching the zone again counts as a "return," not just the zone's
        own formation/immediate aftermath."""
        left = False
        for j in range(zone_end + 1, cur_index):
            lo, hi = float(candles[j]["low"]), float(candles[j]["high"])
            if hi < zone_low or lo > zone_high:
                left = True
        return left

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        swing_highs, swing_lows = self._swings(candles)

        if position_side is not None:
            # structure-break exit: a new swing low under the prior one
            # (long) or a new swing high over the prior one (short)
            if position_side == "long" and len(swing_lows) >= 2 and swing_lows[-1][1] < swing_lows[-2][1]:
                return Signal(Action.EXIT, "structure broke: lower low formed", exit_with_bracket=True)
            if position_side == "short" and len(swing_highs) >= 2 and swing_highs[-1][1] > swing_highs[-2][1]:
                return Signal(Action.EXIT, "structure broke: higher high formed", exit_with_bracket=True)
            return Signal(Action.HOLD, "in position")

        trend = self._trend(swing_highs, swing_lows)
        if trend is None:
            return Signal(Action.HOLD, "no clear market structure trend")

        engulf_idx = len(candles) - 2   # the "rejection candle"; entry fires now, one candle later
        if engulf_idx < 3:
            return Signal(Action.HOLD, "warming up")
        prev, cur = candles[engulf_idx - 1], candles[engulf_idx]

        if trend == "up":
            if not (_is_bull(cur) and _engulfs(prev, cur)):
                return Signal(Action.HOLD, "no bullish rejection at a zone")
            zone = self._find_zone(candles, _is_bull, engulf_idx)
            if zone is None:
                return Signal(Action.HOLD, "no buy zone to react to")
            zone_low, zone_high, zone_end = zone
            lo, hi = float(cur["low"]), float(cur["high"])
            if not (lo <= zone_high and hi >= zone_low):
                return Signal(Action.HOLD, "rejection candle didn't touch the zone")
            if not self._left_and_returned(candles, zone_low, zone_high, zone_end, engulf_idx):
                return Signal(Action.HOLD, "price never left the zone; not a genuine return")
            entry = float(candles[-1]["close"])
            sl = zone_low * (1 - self.sl_buffer_pct / 100)
            tp = entry + (entry - sl) * self.fallback_tp_mult
            return Signal(Action.ENTER_LONG, "bullish rejection at a buy zone in an uptrend",
                          stop_loss=sl, take_profit=tp)

        if not (_is_bear(cur) and _engulfs(prev, cur)):
            return Signal(Action.HOLD, "no bearish rejection at a zone")
        zone = self._find_zone(candles, _is_bear, engulf_idx)
        if zone is None:
            return Signal(Action.HOLD, "no sell zone to react to")
        zone_low, zone_high, zone_end = zone
        lo, hi = float(cur["low"]), float(cur["high"])
        if not (lo <= zone_high and hi >= zone_low):
            return Signal(Action.HOLD, "rejection candle didn't touch the zone")
        if not self._left_and_returned(candles, zone_low, zone_high, zone_end, engulf_idx):
            return Signal(Action.HOLD, "price never left the zone; not a genuine return")
        entry = float(candles[-1]["close"])
        sl = zone_high * (1 + self.sl_buffer_pct / 100)
        tp = entry - (sl - entry) * self.fallback_tp_mult
        return Signal(Action.ENTER_SHORT, "bearish rejection at a sell zone in a downtrend",
                      stop_loss=sl, take_profit=tp)
