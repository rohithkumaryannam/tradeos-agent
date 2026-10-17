"""Shared validation and ATR brackets for the two research candidates."""
import math

from strategies.base import Action, Signal


def period(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise ValueError(f"{name} must be an integer >= 2")
    return value


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def bracket(action, price, volatility, stop_atr, reward_r, name, reason):
    distance = volatility * stop_atr
    direction = 1 if action == Action.ENTER_LONG else -1
    sl, tp = price - direction * distance, price + direction * distance * reward_r
    if not all(math.isfinite(v) and v > 0 for v in (distance, sl, tp)):
        return Signal(Action.HOLD, "ATR bracket is invalid")
    return Signal(action, reason, stop_loss=sl, take_profit=tp, strategy=name,
                  reference_price=price, max_entry_deviation=volatility * 0.5)
