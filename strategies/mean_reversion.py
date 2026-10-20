"""Regime-filtered Bollinger-band mean reversion on closed 15m candles.

This is the live counterpart of ``mean_reversion_backtest.py``.  Position
sizing, portfolio limits, daily loss controls, and order placement remain in
the shared risk/execution engine.
"""
from __future__ import annotations

from statistics import pstdev

from core.indicators import adx, atr, rsi
from strategies.base import Action, BaseStrategy, Signal

name = "mean_reversion"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "15m"
    supported_resolutions = ("15m",)

    def __init__(self, bb_period=20, bb_std=2.0, rsi_period=14,
                 rsi_long_threshold=30.0, rsi_short_threshold=70.0,
                 adx_period=14, adx_max=20.0, atr_period=14,
                 atr_stop_multiple=1.5, max_holding_bars=32, **kw):
        super().__init__(bb_period=bb_period, bb_std=bb_std,
                         rsi_period=rsi_period,
                         rsi_long_threshold=rsi_long_threshold,
                         rsi_short_threshold=rsi_short_threshold,
                         adx_period=adx_period, adx_max=adx_max,
                         atr_period=atr_period,
                         atr_stop_multiple=atr_stop_multiple,
                         max_holding_bars=max_holding_bars, **kw)
        self.bb_period = int(bb_period)
        self.bb_std = float(bb_std)
        self.rsi_period = int(rsi_period)
        self.rsi_long_threshold = float(rsi_long_threshold)
        self.rsi_short_threshold = float(rsi_short_threshold)
        self.adx_period = int(adx_period)
        self.adx_max = float(adx_max)
        self.atr_period = int(atr_period)
        self.atr_stop_multiple = float(atr_stop_multiple)
        self.max_holding_bars = int(max_holding_bars)
        self.required_candles = max(
            self.bb_period,
            self.rsi_period + 1,
            self.adx_period * 2,
            self.atr_period + 1,
        )
        self._entry_bar_time = None

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        if position_side is not None:
            if self._entry_bar_time is not None:
                elapsed = sum(
                    1 for candle in candles
                    if float(candle["time"]) > self._entry_bar_time
                )
                if elapsed >= self.max_holding_bars:
                    self._entry_bar_time = None
                    return Signal(
                        Action.EXIT,
                        f"time-stop: {self.max_holding_bars} bars held",
                        exit_with_bracket=True,
                    )
            return Signal(Action.HOLD, "in position")

        closes = [float(candle["close"]) for candle in candles]
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
            if stop > 0 and middle > price:
                self._entry_bar_time = float(candles[-1]["time"])
                return Signal(
                    Action.ENTER_LONG,
                    f"below lower Bollinger band with RSI {rsi_value:.1f} and ADX {adx_value:.1f}",
                    stop_loss=stop,
                    take_profit=middle,
                )

        if price > upper and rsi_value > self.rsi_short_threshold and middle < price:
            stop = price + self.atr_stop_multiple * atr_value
            self._entry_bar_time = float(candles[-1]["time"])
            return Signal(
                Action.ENTER_SHORT,
                f"above upper Bollinger band with RSI {rsi_value:.1f} and ADX {adx_value:.1f}",
                stop_loss=stop,
                take_profit=middle,
            )

        return Signal(Action.HOLD, "no mean-reversion setup")
