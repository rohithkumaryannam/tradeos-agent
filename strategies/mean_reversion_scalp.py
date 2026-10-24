"""Mean-reversion scalp — same entry as mean_reversion.py (Bollinger-band
extreme + RSI + low-ADX regime filter) but a much closer target: partway
back toward the middle band instead of the full round-trip.

mean_reversion.py's real backtest win rate came in at 22-33%, far below the
55-65% this archetype normally produces — almost certainly because its
target (the full middle band) is often too far to reach before the ATR
stop or 32-bar time-stop fires first. Taking a smaller, easier-to-reach
piece of the reversion mechanically raises win rate (at the cost of a
smaller average win) — that trade-off is the whole point of this variant.

  - Entry: identical to mean_reversion.py (price beyond the band, RSI
    confirming, ADX below adx_max to avoid trading reversion into a trend).
  - Target: entry + target_fraction x (middle - entry), i.e. only
    target_fraction of the way back to the middle band (default 40%).
  - Stop: atr_stop_multiple x ATR from entry (tighter than
    mean_reversion.py's default, since the target is also much closer).
  - Time-stop: exit after max_holding_bars regardless.

Select it in config.yaml:
    strategy: { name: mean_reversion_scalp, params: { resolution: "15m" } }
"""
from __future__ import annotations
from statistics import pstdev

from core.indicators import adx, atr, rsi
from strategies.base import Action, BaseStrategy, Signal

name = "mean_reversion_scalp"


class Strategy(BaseStrategy):
    closed_candles_only = True

    def __init__(self, resolution="15m", bb_period=20, bb_std=2.0, rsi_period=14,
                 rsi_long_threshold=30.0, rsi_short_threshold=70.0,
                 adx_period=14, adx_max=20.0, atr_period=14,
                 atr_stop_multiple=1.0, target_fraction=0.4,
                 max_holding_bars=16, **kw):
        super().__init__(resolution=resolution, bb_period=bb_period, bb_std=bb_std,
                          rsi_period=rsi_period, rsi_long_threshold=rsi_long_threshold,
                          rsi_short_threshold=rsi_short_threshold, adx_period=adx_period,
                          adx_max=adx_max, atr_period=atr_period,
                          atr_stop_multiple=atr_stop_multiple,
                          target_fraction=target_fraction,
                          max_holding_bars=max_holding_bars, **kw)
        self.preferred_resolution = resolution
        self.supported_resolutions = (resolution,)
        self.bb_period, self.bb_std = int(bb_period), float(bb_std)
        self.rsi_period = int(rsi_period)
        self.rsi_long_threshold, self.rsi_short_threshold = float(rsi_long_threshold), float(rsi_short_threshold)
        self.adx_period, self.adx_max = int(adx_period), float(adx_max)
        self.atr_period, self.atr_stop_multiple = int(atr_period), float(atr_stop_multiple)
        self.target_fraction = float(target_fraction)
        self.max_holding_bars = int(max_holding_bars)
        self.required_candles = max(self.bb_period, self.rsi_period + 1,
                                     self.adx_period * 2, self.atr_period + 1)
        self._entry_bar_time = None

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        if position_side is not None:
            if self._entry_bar_time is not None:
                elapsed = sum(1 for c in candles if float(c["time"]) > self._entry_bar_time)
                if elapsed >= self.max_holding_bars:
                    self._entry_bar_time = None
                    return Signal(Action.EXIT, f"time-stop: {self.max_holding_bars} bars held",
                                  exit_with_bracket=True)
            return Signal(Action.HOLD, "in position")

        closes = [float(c["close"]) for c in candles]
        window = closes[-self.bb_period:]
        middle = sum(window) / self.bb_period
        deviation = pstdev(window)
        upper = middle + self.bb_std * deviation
        lower = middle - self.bb_std * deviation
        price = closes[-1]

        rsi_value = rsi(closes, self.rsi_period)[-1]
        adx_value = adx(candles, self.adx_period)[-1]
        atr_value = atr(candles, self.atr_period)[-1]
        if None in (rsi_value, adx_value, atr_value):
            return Signal(Action.HOLD, "warming up")
        if adx_value >= self.adx_max:
            return Signal(Action.HOLD, f"trend regime (ADX {adx_value:.1f})")

        if price < lower and rsi_value < self.rsi_long_threshold and middle > price:
            stop = price - self.atr_stop_multiple * atr_value
            target = price + self.target_fraction * (middle - price)
            if stop > 0:
                self._entry_bar_time = float(candles[-1]["time"])
                return Signal(Action.ENTER_LONG,
                              f"below lower band, RSI {rsi_value:.1f}, ADX {adx_value:.1f}, "
                              f"{self.target_fraction*100:.0f}% reversion target",
                              stop_loss=stop, take_profit=target)

        if price > upper and rsi_value > self.rsi_short_threshold and middle < price:
            stop = price + self.atr_stop_multiple * atr_value
            target = price - self.target_fraction * (price - middle)
            self._entry_bar_time = float(candles[-1]["time"])
            return Signal(Action.ENTER_SHORT,
                          f"above upper band, RSI {rsi_value:.1f}, ADX {adx_value:.1f}, "
                          f"{self.target_fraction*100:.0f}% reversion target",
                          stop_loss=stop, take_profit=target)

        return Signal(Action.HOLD, "no setup")
