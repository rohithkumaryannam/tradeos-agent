"""No-op strategy: never enters, never exits early. Assign it to a symbol to
stop new entries there while leaving everything else about that market slot
(the combo tag, its risk cap, and any already-open position's exchange-side
bracket) untouched — the position still closes on its own SL/TP, the bot
just won't open a new one after.

Select it per-symbol via the dashboard's "Assign strategies per coin" panel,
or in config.yaml:
    { name: paused, tag: ..., symbols: [...] }
"""
from __future__ import annotations

from strategies.base import BaseStrategy, Signal, Action

name = "paused"


class Strategy(BaseStrategy):
    required_candles = 1

    def generate(self, candles, position_side):
        if position_side is not None:
            return Signal(Action.HOLD, "in position (strategy paused, no new signals)")
        return Signal(Action.HOLD, "strategy paused — no entries")
