"""Donchian channel breakout, with a volume filter, ADX trend filter, and a
static ATR stop. The real exit is a trailing channel-cross, not a fixed
take-profit — see `fallback_tp_mult` below.

  - Entry channel = N-period Donchian high/low (default 20), using the prior
    N candles only (today's candle isn't part of its own channel).
  - Breakout: close above the entry-channel high -> long; below the low -> short.
  - Volume filter: the breakout candle's volume must exceed
    `volume_mult` x its 20-candle rolling average, to cut false breakouts.
  - ADX(14) > adx_threshold: optional trend-strength filter.
  - Exit channel = M-period Donchian high/low (default 10, shorter than the
    entry channel) — trailing exit: close back through it in the opposite
    direction means the breakout is failing/reversing.
  - Stop-loss = atr_stop_mult x ATR14 from entry, held static (not trailed —
    this is a trend-initiation system; a tight trailing stop would cut off
    the big winners it depends on).
  - Take-profit is NOT the real exit (the trailing channel-cross above is);
    it's set far away (fallback_tp_mult x stop distance) purely because the
    risk engine requires a bracket. In practice price should almost never
    reach it — the channel exit fires first.

This is a trend-initiation system, complementary to an EMA pullback system:
catches moves earlier but has a lower win rate (commonly 35-45%) and depends
on a few large winners to offset many small losses. Long losing streaks are
expected even when the edge is real.

Ported from a Freqtrade strategy. Ran on 15m candles originally; works on
higher timeframes too if you want a slower/less noisy variant — adjust
supported_resolutions below.

Select it in config.yaml:
    strategy: { name: donchian_breakout, params: { entry_period: 20 } }
Or alongside another strategy:
    strategy: { name: combo, params: { subs: [ema_crossover, donchian_breakout] } }
"""
from __future__ import annotations

from core.indicators import adx, atr
from strategies.base import BaseStrategy, Signal, Action

name = "donchian_breakout"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "15m"
    supported_resolutions = ("15m", "1h", "4h", "1d")

    def __init__(self, entry_period=20, exit_period=10, volume_mult=1.2,
                 atr_period=14, atr_stop_mult=2.0, use_adx_filter=True,
                 adx_period=14, adx_threshold=20, fallback_tp_mult=8.0, **kw):
        super().__init__(entry_period=entry_period, exit_period=exit_period,
                          volume_mult=volume_mult, atr_period=atr_period,
                          atr_stop_mult=atr_stop_mult, use_adx_filter=use_adx_filter,
                          adx_period=adx_period, adx_threshold=adx_threshold,
                          fallback_tp_mult=fallback_tp_mult, **kw)
        self.entry_period, self.exit_period = entry_period, exit_period
        self.volume_mult = volume_mult
        self.atr_period, self.atr_stop_mult = atr_period, atr_stop_mult
        self.use_adx_filter, self.adx_period, self.adx_threshold = \
            use_adx_filter, adx_period, adx_threshold
        self.fallback_tp_mult = fallback_tp_mult
        self.required_candles = max(entry_period, exit_period, atr_period,
                                     adx_period * 2, 20) + 10

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        volumes = [float(c.get("volume") or 0) for c in candles]
        price, vol = closes[-1], volumes[-1]

        # Donchian channels use the PRIOR N candles only (shift by 1: exclude
        # the current/latest candle from its own channel).
        entry_hi = max(highs[-self.entry_period - 1:-1])
        entry_lo = min(lows[-self.entry_period - 1:-1])
        exit_hi = max(highs[-self.exit_period - 1:-1])
        exit_lo = min(lows[-self.exit_period - 1:-1])

        if position_side == "long" and price < exit_lo:
            return Signal(Action.EXIT, "closed back below the exit channel low")
        if position_side == "short" and price > exit_hi:
            return Signal(Action.EXIT, "closed back above the exit channel high")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        if vol <= 0:
            return Signal(Action.HOLD, "no volume")
        vol_avg = sum(volumes[-20:]) / 20
        volume_ok = vol > vol_avg * self.volume_mult

        adx_vals = adx(candles, self.adx_period)
        trend_ok = True
        if self.use_adx_filter:
            if adx_vals[-1] is None:
                return Signal(Action.HOLD, "warming up")
            trend_ok = adx_vals[-1] > self.adx_threshold

        atr_vals = atr(candles, self.atr_period)
        atr_val = atr_vals[-1]
        if atr_val is None:
            return Signal(Action.HOLD, "warming up")
        stop_dist = self.atr_stop_mult * atr_val

        if price > entry_hi and volume_ok and trend_ok:
            sl = price - stop_dist
            tp = price + stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_LONG,
                          f"breakout above {self.entry_period}-period Donchian high, "
                          f"vol {vol:.0f} > {self.volume_mult}x avg" +
                          (f", ADX {adx_vals[-1]:.1f}" if self.use_adx_filter else ""),
                          stop_loss=sl, take_profit=tp)
        if price < entry_lo and volume_ok and trend_ok:
            sl = price + stop_dist
            tp = price - stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_SHORT,
                          f"breakout below {self.entry_period}-period Donchian low, "
                          f"vol {vol:.0f} > {self.volume_mult}x avg" +
                          (f", ADX {adx_vals[-1]:.1f}" if self.use_adx_filter else ""),
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "no breakout")
