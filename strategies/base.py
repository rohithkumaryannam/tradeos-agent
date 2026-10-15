"""Strategy interface. Implement your own rules by subclassing BaseStrategy.

A strategy's only job is to turn candles + current position into a Signal.
It does NOT size trades, place orders, or manage risk — the risk engine and
executor handle that. Keep strategies pure and side-effect free.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    HOLD = "HOLD"          # do nothing
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"          # close current position (strategy-driven exit)


@dataclass
class Signal:
    action: Action
    reason: str = ""       # human-readable why, for the trade log
    # Optional per-trade SL/TP PRICES. Most strategies leave these None and the
    # risk engine applies the config % stop/target. A strategy whose edge is its
    # own stop placement (e.g. at a structural level) can set them here; the risk
    # engine then sizes the position off that custom stop distance.
    stop_loss: float | None = None
    take_profit: float | None = None


class BaseStrategy(ABC):
    def __init__(self, **params):
        self.params = params

    @abstractmethod
    def generate(self, candles: list[dict], position_side: str | None) -> Signal:
        """
        candles: list of OHLC dicts, oldest -> newest.
        position_side: "long", "short", or None if flat.
        Return a Signal.
        """
        ...
