"""Combo meta-strategy: run several strategies at once, each optionally on
its own timeframe.

While flat, it polls each sub-strategy in order and takes the first entry
signal — tagging the trade with that sub's tag (shows up as the Strategy
column in the trade journal and the per-strategy performance table). While
in a position, only the strategy that OPENED it can signal an exit (the
others are ignored until flat), so ownership stays coherent. SL/TP, sizing
and the daily cap are still enforced by the risk engine exactly as for a
single strategy.

Each sub is one of:
  - a plain string (a strategy module name) — loaded with its own class
    defaults, tagged by that name. Runs on the base resolution (the first
    closed-candle sub's preferred_resolution, usually also the agent's
    market.resolution).
  - a dict: { name, params?, tag? } — params configure that instance (e.g.
    a different timeframe); tag is the attribution label (defaults to name
    — set it explicitly when the same strategy module appears twice, as for
    two ema_crossover instances on different timeframes, so trades/analytics
    can tell them apart).

A sub whose own preferred_resolution differs from the base one fetches its
OWN candles independently (via the agent's fetch_candles, injected as
_fetch_candles/_symbol — see core/agent.py._build_strategy) instead of
reusing the shared candle feed, so strategies on different timeframes can
run genuinely in parallel rather than all being forced onto one resolution.

Select it in config.yaml:
    strategy: { name: combo, params: { subs: [ema_crossover, donchian_breakout] } }
Or with per-sub params/timeframes:
    strategy:
      name: combo
      params:
        subs:
          - { name: ema_crossover, params: { resolution: "5m" }, tag: ema_5m }
          - { name: ema_crossover, params: { resolution: "15m" }, tag: ema_15m }
"""
from __future__ import annotations
import time

from core import db
from core.agent import _RES_SECONDS
from strategies.base import BaseStrategy, Signal, Action
from strategies.loader import load_strategy

name = "combo"

_DEFAULT_SUBS = ["ema_crossover", "donchian_breakout"]


class Strategy(BaseStrategy):
    def __init__(self, subs=None, _symbol=None, _fetch_candles=None, **kw):
        super().__init__(subs=subs, **kw)
        specs = subs or _DEFAULT_SUBS
        self._symbol, self._fetch_candles = _symbol, _fetch_candles

        # Build every sub first (unfiltered) — this set decides the shared
        # metadata (resolution/required_candles) so every market's combo
        # instance reports the SAME values and passes the agent's per-market
        # resolution gate consistently, regardless of which subs actually
        # apply to that market. Filtering by `symbols` only affects which
        # subs actually trade below, not this metadata.
        all_subs = []
        for spec in specs:
            if isinstance(spec, str):
                sub_name, params, tag, symbols = spec, {}, spec, None
            else:
                sub_name = spec["name"]
                params = spec.get("params") or {}
                tag = spec.get("tag", sub_name)
                symbols = spec.get("symbols")
            all_subs.append((tag, load_strategy(sub_name, params), symbols))

        # `symbols` on a sub spec dedicates it to specific market(s) — e.g.
        # giving ema_5m and ema_15m one symbol each so they never compete for
        # the same position and both genuinely get to trade, for a fair
        # comparison. A sub with no `symbols` restriction applies everywhere.
        self.subs = [(tag, strat) for tag, strat, symbols in all_subs
                     if not symbols or (_symbol and _symbol in symbols)]
        if not self.subs:
            # misconfigured (symbols excludes every market) — fail open
            # rather than silently trading nothing.
            self.subs = [(tag, strat) for tag, strat, _ in all_subs]

        self.closed_candles_only = any(getattr(s, "closed_candles_only", False) for _, s, _ in all_subs)
        # "base" = whichever closed-candle sub runs on the agent's own shared
        # candle feed (the first one, by convention/order); every other
        # closed-candle sub fetches its own data independently if its
        # resolution differs, so it doesn't need to match the base at all.
        base = next((s for _, s, _ in all_subs if getattr(s, "closed_candles_only", False)), None)
        if base is not None:
            self.preferred_resolution = getattr(base, "preferred_resolution", "15m")
            self.supported_resolutions = getattr(base, "supported_resolutions", (self.preferred_resolution,))
            self.required_candles = max(
                getattr(s, "required_candles", 1) for _, s, _ in all_subs
                if getattr(s, "preferred_resolution", self.preferred_resolution) == self.preferred_resolution
            )
        else:
            self.required_candles = max(getattr(s, "required_candles", 1) for _, s, _ in all_subs)
        self._owner: str | None = None

    def _candles_for(self, strat, base_candles):
        """base_candles at whatever resolution the agent fetched this cycle;
        if `strat` prefers a different one and we can fetch independently,
        pull its own (closed-candles-only) data instead of reusing base."""
        res = getattr(strat, "preferred_resolution", None)
        if not res or not self._fetch_candles or not self._symbol:
            return base_candles
        sec = _RES_SECONDS.get(res)
        base_step = (base_candles[1]["time"] - base_candles[0]["time"]
                     if len(base_candles) >= 2 else None)
        if sec is None or sec == base_step:
            return base_candles
        raw = self._fetch_candles(self._symbol, res)
        if getattr(strat, "closed_candles_only", False):
            now = time.time()
            raw = [c for c in raw if float(c["time"]) + sec <= now]
        return raw

    def generate(self, candles, position_side):
        if position_side is None:
            self._owner = None
            for nm, strat in self.subs:
                sub_candles = self._candles_for(strat, candles)
                if len(sub_candles) < 2:
                    continue
                sig = strat.generate(sub_candles, None)
                if sig.action in (Action.ENTER_LONG, Action.ENTER_SHORT):
                    self._owner = nm
                    sig.strategy = nm
                    sig.reason = f"[{nm}] {sig.reason}"
                    return sig
            return Signal(Action.HOLD, "no setup (combo)")

        # in a position: only the owning strategy manages the exit. A fresh
        # combo instance (e.g. after a restart) doesn't remember who opened
        # an already-open position in memory — recover it from what the
        # agent persisted at entry time, or this position silently never
        # gets a reversal/exit signal again until it closes on its own.
        if self._owner is None and self._symbol:
            persisted = db.load_position_bracket(self._symbol)
            if persisted and persisted.get("strategy") in dict(self.subs):
                self._owner = persisted["strategy"]
        owner = dict(self.subs).get(self._owner)
        if owner is None:
            return Signal(Action.HOLD, "in position")
        sub_candles = self._candles_for(owner, candles)
        sig = owner.generate(sub_candles, position_side)
        if sig.action == Action.EXIT:
            sig.strategy = self._owner
            sig.reason = f"[{self._owner}] {sig.reason}"
            self._owner = None
            return sig
        return Signal(Action.HOLD, f"in position ({self._owner})")
