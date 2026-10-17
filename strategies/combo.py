"""Combo meta-strategy: run several strategies at once.

While flat, it polls each sub-strategy in order and takes the first entry signal —
tagging the trade with the strategy that fired it. While in a position, only the
strategy that OPENED it can signal an exit (the others are ignored until flat),
so ownership stays coherent. SL/TP, sizing and the daily cap are still enforced by
the risk engine exactly as for a single strategy.

Select it in config.yaml:
    strategy: { name: combo, params: { subs: [ny_range_fakeout, ema_crossover,
                                                donchian_breakout, time_series_momentum] } }
"""
from __future__ import annotations

from strategies.base import BaseStrategy, Signal, Action
from strategies.loader import load_strategy

name = "combo"

_DEFAULT_SUBS = [
    "ny_range_fakeout",
    "ema_crossover",
    "donchian_breakout",
    "time_series_momentum",
]


class Strategy(BaseStrategy):
    def __init__(self, subs=None, **kw):
        super().__init__(subs=subs, **kw)
        names = subs or _DEFAULT_SUBS
        self.subs = [(n, load_strategy(n, {})) for n in names]
        self.closed_candles_only = any(getattr(s, "closed_candles_only", False) for _, s in self.subs)
        self.required_candles = max(getattr(s, "required_candles", 1) for _, s in self.subs)
        if self.closed_candles_only:
            self.preferred_resolution = "1h"
            self.supported_resolutions = ("1h", "4h", "1d")
        self._owner: str | None = None

    def generate(self, candles, position_side):
        if position_side is None:
            self._owner = None
            for nm, strat in self.subs:
                sig = strat.generate(candles, None)
                if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT):
                    self._owner = nm
                    sig.strategy = nm
                    sig.reason = f"[{nm}] {sig.reason}"
                    return sig
            return Signal(Action.HOLD, "no setup (combo)")

        # in a position: only the owning strategy manages the exit
        owner = dict(self.subs).get(self._owner)
        if owner is None:
            return Signal(Action.HOLD, "in position")
        sig = owner.generate(candles, position_side)
        if sig.action == Action.EXIT:
            sig.strategy = self._owner
            sig.reason = f"[{self._owner}] {sig.reason}"
            self._owner = None
            return sig
        return Signal(Action.HOLD, f"in position ({self._owner})")
