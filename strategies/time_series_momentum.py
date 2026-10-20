"""Multi-horizon momentum with EMA confirmation and ATR brackets."""
from __future__ import annotations

import math

from core.indicators import atr, closes, ema
from strategies.base import Action, BaseStrategy, Signal

name = "time_series_momentum"


def _period(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise ValueError(f"{label} must be an integer >= 2")
    return value


def _positive(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "1h"
    supported_resolutions = ("1h", "4h", "1d")

    def __init__(self, horizons=(24, 72, 168), trend_period=200,
                 atr_period=14, min_move_atr=0.5, stop_atr=2.0,
                 reward_r=3.0, **kw):
        super().__init__(horizons=horizons, trend_period=trend_period,
                         atr_period=atr_period, min_move_atr=min_move_atr,
                         stop_atr=stop_atr, reward_r=reward_r, **kw)
        if not isinstance(horizons, (list, tuple)) or not horizons:
            raise ValueError("horizons must be a nonempty list of periods")
        self.horizons = tuple(_period(value, "horizon") for value in horizons)
        if tuple(sorted(set(self.horizons))) != self.horizons:
            raise ValueError("horizons must be distinct and ascending")
        self.trend_period = _period(trend_period, "trend_period")
        self.atr_period = _period(atr_period, "atr_period")
        self.min_move_atr = _positive(min_move_atr, "min_move_atr")
        self.stop_atr = _positive(stop_atr, "stop_atr")
        self.reward_r = _positive(reward_r, "reward_r")
        self.required_candles = max(
            max(self.horizons) + 1, self.trend_period, self.atr_period + 1,
        )

    def _entry(self, action, price, volatility, reason):
        distance = volatility * self.stop_atr
        direction = 1 if action == Action.ENTER_LONG else -1
        stop = price - direction * distance
        target = price + direction * distance * self.reward_r
        if not all(math.isfinite(v) and v > 0 for v in (distance, stop, target)):
            return Signal(Action.HOLD, "ATR bracket is invalid")
        return Signal(
            action,
            reason,
            stop_loss=stop,
            take_profit=target,
            reference_price=price,
            max_entry_deviation=volatility * 0.5,
        )

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(
                Action.HOLD,
                f"warming up: need {self.required_candles} closed candles",
            )
        prices = closes(candles)
        price = prices[-1]
        moves = [price - prices[-1 - horizon] for horizon in self.horizons]
        trend = ema(prices, self.trend_period)[-1]

        if position_side == "long" and (moves[0] <= 0 or price < trend):
            return Signal(
                Action.EXIT,
                "short-horizon momentum or EMA trend reversed",
                exit_with_bracket=True,
            )
        if position_side == "short" and (moves[0] >= 0 or price > trend):
            return Signal(
                Action.EXIT,
                "short-horizon momentum or EMA trend reversed",
                exit_with_bracket=True,
            )
        if position_side:
            return Signal(Action.HOLD, "momentum intact")

        volatility = atr(candles, self.atr_period)[-1]
        if not volatility or volatility <= 0:
            return Signal(Action.HOLD, "no usable volatility")
        threshold = self.min_move_atr * volatility
        horizons = ",".join(map(str, self.horizons))
        reason = (
            f"momentum aligned across {horizons} bars with "
            f"EMA{self.trend_period} confirmation"
        )
        if all(move > threshold for move in moves) and price > trend:
            return self._entry(Action.ENTER_LONG, price, volatility, reason)
        if all(move < -threshold for move in moves) and price < trend:
            return self._entry(Action.ENTER_SHORT, price, volatility, reason)
        return Signal(Action.HOLD, "momentum horizons are not aligned")
