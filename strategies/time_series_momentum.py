"""Multi-horizon directional momentum with ATR brackets and EMA confirmation.

An hourly adaptation of a documented strategy family; its exact parameters
and returns have not been validated by the source papers.
"""
from core.indicators import atr, closes, ema
from strategies.base import Action, BaseStrategy, Signal
from strategies._trend import bracket, period, positive

name = "time_series_momentum"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "1h"
    supported_resolutions = ("1h", "4h", "1d")

    def __init__(self, horizons=(24, 72, 168), trend_period=200, atr_period=14,
                 min_move_atr=0.5, stop_atr=2.0, reward_r=3.0):
        super().__init__(horizons=horizons, trend_period=trend_period, atr_period=atr_period,
                         min_move_atr=min_move_atr, stop_atr=stop_atr, reward_r=reward_r)
        if not isinstance(horizons, (list, tuple)) or not horizons:
            raise ValueError("horizons must be a nonempty list of periods")
        self.horizons = tuple(period(h, "horizon") for h in horizons)
        if tuple(sorted(set(self.horizons))) != self.horizons:
            raise ValueError("horizons must be distinct and ascending")
        self.trend_period = period(trend_period, "trend_period")
        self.atr_period = period(atr_period, "atr_period")
        self.min_move_atr = positive(min_move_atr, "min_move_atr")
        self.stop_atr = positive(stop_atr, "stop_atr")
        self.reward_r = positive(reward_r, "reward_r")
        self.required_candles = max(max(self.horizons)+1, trend_period, atr_period+1)

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, f"warming up: need {self.required_candles} closed candles")
        c = closes(candles)
        price = c[-1]
        moves = [price - c[-1-h] for h in self.horizons]
        trend = ema(c, self.trend_period)[-1]
        if position_side == "long" and (moves[0] <= 0 or price < trend):
            return Signal(Action.EXIT, "short-horizon momentum or EMA trend reversed", strategy=name, exit_with_bracket=True)
        if position_side == "short" and (moves[0] >= 0 or price > trend):
            return Signal(Action.EXIT, "short-horizon momentum or EMA trend reversed", strategy=name, exit_with_bracket=True)
        if position_side:
            return Signal(Action.HOLD, "momentum intact; bracket and momentum exit active")
        volatility = atr(candles, self.atr_period)[-1]
        if not volatility or volatility <= 0:
            return Signal(Action.HOLD, "no usable volatility")
        threshold = self.min_move_atr * volatility
        reason = f"momentum aligned across {','.join(map(str, self.horizons))} bars with EMA{self.trend_period} confirmation"
        if all(move > threshold for move in moves) and price > trend:
            return bracket(Action.ENTER_LONG, price, volatility, self.stop_atr, self.reward_r, name, reason)
        if all(move < -threshold for move in moves) and price < trend:
            return bracket(Action.ENTER_SHORT, price, volatility, self.stop_atr, self.reward_r, name, reason)
        return Signal(Action.HOLD, "waiting for all momentum horizons to agree beyond the volatility threshold")
