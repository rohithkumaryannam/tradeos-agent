"""Bollinger Band squeeze breakout — trades WITH a breakout out of a
low-volatility compression, the opposite character from mean_reversion.py
(which fades band extremes). Compression ("squeeze") is measured by
bandwidth = (upper - lower) / middle; when the current bandwidth sits in the
bottom `squeeze_pct` of its own rolling history, volatility has compressed
and a breakout is more likely to be a genuine expansion than noise.

  - Bollinger bands: bb_period SMA +/- bb_std standard deviations.
  - Squeeze: current bandwidth <= squeeze_pct-th percentile of the trailing
    `lookback` bandwidth values (default: bottom 20% of the last 100 bars).
  - Breakout: close above the upper band (while squeezed) -> long; below
    the lower band (while squeezed) -> short.
  - Volume confirmation: breakout candle's volume > volume_mult x its
    20-candle rolling average (same convention as donchian_breakout.py).
  - Fixed SL/TP from entry (ATR-based stop, fixed-R target) since this is a
    single breakout trade, not a trend-following system with a trailing exit.

Select it in config.yaml:
    strategy: { name: bb_squeeze_breakout, params: { resolution: "15m" } }
"""
from __future__ import annotations
from statistics import pstdev

from core.indicators import atr
from strategies.base import BaseStrategy, Signal, Action

name = "bb_squeeze_breakout"


class Strategy(BaseStrategy):
    closed_candles_only = True

    def __init__(self, resolution="15m", bb_period=20, bb_std=2.0,
                 squeeze_lookback=100, squeeze_pct=20, volume_mult=1.2,
                 atr_period=14, stop_atr_mult=1.5, reward_risk=2.0, **kw):
        super().__init__(resolution=resolution, bb_period=bb_period, bb_std=bb_std,
                          squeeze_lookback=squeeze_lookback, squeeze_pct=squeeze_pct,
                          volume_mult=volume_mult, atr_period=atr_period,
                          stop_atr_mult=stop_atr_mult, reward_risk=reward_risk, **kw)
        self.preferred_resolution = resolution
        self.supported_resolutions = (resolution,)
        self.bb_period, self.bb_std = bb_period, float(bb_std)
        self.squeeze_lookback, self.squeeze_pct = squeeze_lookback, squeeze_pct
        self.volume_mult = volume_mult
        self.atr_period, self.stop_atr_mult, self.reward_risk = atr_period, stop_atr_mult, reward_risk
        self.required_candles = max(bb_period, squeeze_lookback + bb_period, atr_period, 20) + 6

    def _bandwidth(self, closes, i):
        window = closes[i - self.bb_period + 1:i + 1]
        middle = sum(window) / self.bb_period
        deviation = pstdev(window)
        upper = middle + self.bb_std * deviation
        lower = middle - self.bb_std * deviation
        return (upper - lower) / middle, upper, lower

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        closes = [float(c["close"]) for c in candles]
        volumes = [float(c.get("volume") or 0) for c in candles]
        price, vol = closes[-1], volumes[-1]

        # Squeeze is judged as of the PRIOR bar's bands (before this candle's
        # own move could have already expanded them) — the breakout is this
        # candle's close breaking those prior bands, not a bar that is
        # simultaneously "squeezed" and "breaking out" by construction.
        n = len(candles)
        bandwidths = [self._bandwidth(closes, i)[0] for i in
                      range(n - 1 - self.squeeze_lookback, n - 1)]
        prior_bw, upper, lower = self._bandwidth(closes, n - 2)
        threshold = sorted(bandwidths)[int(len(bandwidths) * self.squeeze_pct / 100)]
        squeezed = prior_bw <= threshold

        if not squeezed:
            return Signal(Action.HOLD, "not in a volatility squeeze")

        if vol <= 0:
            return Signal(Action.HOLD, "no volume")
        vol_avg = sum(volumes[-20:]) / 20
        volume_ok = vol > vol_avg * self.volume_mult
        if not volume_ok:
            return Signal(Action.HOLD, "squeeze without volume confirmation")

        atr_vals = atr(candles, self.atr_period)
        atr_val = atr_vals[-1]
        if atr_val is None:
            return Signal(Action.HOLD, "warming up")
        stop_dist = self.stop_atr_mult * atr_val

        if price > upper:
            sl = price - stop_dist
            tp = price + stop_dist * self.reward_risk
            return Signal(Action.ENTER_LONG,
                          f"squeeze breakout above upper band, vol confirmed",
                          stop_loss=sl, take_profit=tp)
        if price < lower:
            sl = price + stop_dist
            tp = price - stop_dist * self.reward_risk
            return Signal(Action.ENTER_SHORT,
                          f"squeeze breakout below lower band, vol confirmed",
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "squeezed but no breakout yet")
