"""Closed-bar channel breakout with a trend filter and volatility-based risk.

Research-inspired candidate, not a reproduction of a published return series.
Default research timeframe: 1h. See docs/strategy-research.md.
"""
from core.indicators import atr, closes, ema
from strategies.base import Action, BaseStrategy, Signal
from strategies._trend import bracket, period, positive

name = "donchian_breakout"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "1h"
    supported_resolutions = ("1h", "4h", "1d")

    def __init__(self, entry_period=55, exit_period=20, trend_period=200,
                 atr_period=14, stop_atr=2.0, reward_r=3.0):
        super().__init__(entry_period=entry_period, exit_period=exit_period,
                         trend_period=trend_period, atr_period=atr_period,
                         stop_atr=stop_atr, reward_r=reward_r)
        self.entry_period = period(entry_period, "entry_period")
        self.exit_period = period(exit_period, "exit_period")
        self.trend_period = period(trend_period, "trend_period")
        self.atr_period = period(atr_period, "atr_period")
        self.stop_atr = positive(stop_atr, "stop_atr")
        self.reward_r = positive(reward_r, "reward_r")
        if exit_period >= entry_period:
            raise ValueError("exit_period must be shorter than entry_period")
        self.required_candles = max(entry_period + 1, trend_period + 1, atr_period + 1)

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, f"warming up: need {self.required_candles} closed candles")
        c = closes(candles)
        price = c[-1]
        # Exclude the signal candle from the channel: no look-ahead.
        channel = candles[-self.entry_period-1:-1]
        upper = max(float(b["high"]) for b in channel)
        lower = min(float(b["low"]) for b in channel)
        exit_channel = candles[-self.exit_period-1:-1]
        if position_side == "long" and price < min(float(b["low"]) for b in exit_channel):
            return Signal(Action.EXIT, "closed below exit channel", strategy=name, exit_with_bracket=True)
        if position_side == "short" and price > max(float(b["high"]) for b in exit_channel):
            return Signal(Action.EXIT, "closed above exit channel", strategy=name, exit_with_bracket=True)
        if position_side:
            return Signal(Action.HOLD, "channel trend intact; bracket and channel exit active")
        trend = ema(c, self.trend_period)
        volatility = atr(candles, self.atr_period)[-1]
        if not volatility or volatility <= 0:
            return Signal(Action.HOLD, "no usable volatility")
        if price > upper and price > trend[-1] and trend[-1] > trend[-2]:
            return bracket(Action.ENTER_LONG, price, volatility, self.stop_atr, self.reward_r,
                           name, f"{self.entry_period}-bar upside breakout above rising EMA{self.trend_period}")
        if price < lower and price < trend[-1] and trend[-1] < trend[-2]:
            return bracket(Action.ENTER_SHORT, price, volatility, self.stop_atr, self.reward_r,
                           name, f"{self.entry_period}-bar downside breakout below falling EMA{self.trend_period}")
        return Signal(Action.HOLD, "waiting for a closed-bar channel breakout with trend confirmation")
