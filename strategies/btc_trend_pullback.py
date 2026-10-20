"""BTC Trend-Pullback, on 15m candles — a live port of the precise, fully
specified logic in strategies/btc_trend_pullback_backtest.py (that file is
a standalone offline backtester using pandas/numpy; this is the same rules
reimplemented in the live bot's pure-Python strategy framework). Unlike the
BTC BMS strategy, nothing here is ambiguous — every rule below maps exactly
to an explicit condition in that script.

Rules:
  - Uptrend:   close > EMA200, EMA50 > EMA200, EMA20 > EMA50, ADX14 >= adx_min
  - Downtrend: close < EMA200, EMA50 < EMA200, EMA20 < EMA50, ADX14 >= adx_min
  - Pullback (uptrend): the PRIOR closed candle's low touched down to within
    0.1x ATR14 of its own EMA20 (mirror for downtrend, using highs).
  - Confirmation (current candle): bullish (close>open) and closes above
    EMA20 for a long; bearish and closes below EMA20 for a short.
  - Stop = the lowest low (long) / highest high (short) of the last 4
    candles, offset by atr_stop_buffer x ATR14 — a structure stop with an
    ATR buffer, set once at entry, held static.
  - Target = entry +/- target_r x stop distance (fixed R-multiple, default
    2R, matching the backtest script's target_r=2.0).
  - Time-stop: force-exit after max_holding_bars candles (default 32 = 8h
    on 15m) if neither the stop nor target has been hit. Tracked as simple
    instance state (the entry candle's timestamp) — not persisted across a
    restart, unlike the SL/TP bracket; a restart just means this particular
    safety net resets, the stop/target/exit-on-structure-break (there isn't
    one here; this strategy has no cross-based exit) keep working regardless.

Deliberately NOT ported from the backtest script (handled generically by
the live risk engine instead, for every strategy, not duplicated here):
  - Position sizing (risk_per_trade, fee/slippage-aware quantity) — the risk
    engine already sizes off the stop distance via strategy_risk_pct.
  - Daily-loss halt / consecutive-loss halt — already global risk-config
    settings (max_daily_loss_pct, max_consecutive_losses), not re-applied
    per strategy.
  - Funding-rate cost modeling — the backtest script treats it as a research
    input; nothing in the live agent models funding cashflow per position.

One real behavioral difference from the backtest's own assumption: the
script fills at the NEXT bar's open (a deliberate 1-candle execution lag).
The live agent doesn't delay entries by an extra candle (same as every
other strategy here, e.g. ema_crossover, donchian_breakout) — it fills at
current price as soon as the signal fires.

Select it in config.yaml:
    strategy: { name: btc_trend_pullback, params: {} }
"""
from __future__ import annotations

from core.indicators import ema, atr, adx
from strategies.base import BaseStrategy, Signal, Action

name = "btc_trend_pullback"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "15m"
    supported_resolutions = ("15m",)

    def __init__(self, adx_min=20.0, atr_stop_buffer=0.2, target_r=2.0,
                 max_holding_bars=32, pullback_atr_mult=0.1, structure_window=4, **kw):
        super().__init__(adx_min=adx_min, atr_stop_buffer=atr_stop_buffer, target_r=target_r,
                          max_holding_bars=max_holding_bars, pullback_atr_mult=pullback_atr_mult,
                          structure_window=structure_window, **kw)
        self.adx_min = adx_min
        self.atr_stop_buffer = atr_stop_buffer
        self.target_r = target_r
        self.max_holding_bars = max_holding_bars
        self.pullback_atr_mult = pullback_atr_mult
        self.structure_window = structure_window
        self.required_candles = 220
        self._entry_bar_time = None   # instance state for the time-stop; see module docstring

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        closes = [float(c["close"]) for c in candles]
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        ema200 = ema(closes, 200)
        atr14 = atr(candles, 14)
        adx14 = adx(candles, 14)

        if None in (ema20[-1], ema50[-1], ema200[-1], atr14[-1], adx14[-1], ema20[-2]):
            return Signal(Action.HOLD, "warming up")

        if position_side is not None:
            if self._entry_bar_time is not None:
                elapsed = sum(1 for c in candles if float(c["time"]) > self._entry_bar_time)
                if elapsed >= self.max_holding_bars:
                    self._entry_bar_time = None
                    return Signal(Action.EXIT, f"time-stop: {self.max_holding_bars} bars held",
                                  exit_with_bracket=True)
            return Signal(Action.HOLD, "in position")

        price = closes[-1]
        prev_low, prev_high = float(candles[-2]["low"]), float(candles[-2]["high"])
        cur_open, cur_close = float(candles[-1]["open"]), float(candles[-1]["close"])

        uptrend = price > ema200[-1] and ema50[-1] > ema200[-1] and ema20[-1] > ema50[-1] and adx14[-1] >= self.adx_min
        downtrend = price < ema200[-1] and ema50[-1] < ema200[-1] and ema20[-1] < ema50[-1] and adx14[-1] >= self.adx_min

        if uptrend:
            pullback = prev_low <= ema20[-2] + self.pullback_atr_mult * atr14[-2] if atr14[-2] is not None else False
            confirmed = cur_close > cur_open and cur_close > ema20[-1]
            if pullback and confirmed:
                window = candles[-(self.structure_window + 1):-1]
                structure_low = min(float(c["low"]) for c in window)
                stop = structure_low - self.atr_stop_buffer * atr14[-1]
                distance = price - stop
                if distance > 0:
                    target = price + self.target_r * distance
                    self._entry_bar_time = float(candles[-1]["time"])
                    return Signal(Action.ENTER_LONG,
                                  f"uptrend pullback to EMA20 (ADX {adx14[-1]:.1f}), bullish confirmation",
                                  stop_loss=stop, take_profit=target)
            return Signal(Action.HOLD, "uptrend, no confirmed pullback entry yet")

        if downtrend:
            pullback = prev_high >= ema20[-2] - self.pullback_atr_mult * atr14[-2] if atr14[-2] is not None else False
            confirmed = cur_close < cur_open and cur_close < ema20[-1]
            if pullback and confirmed:
                window = candles[-(self.structure_window + 1):-1]
                structure_high = max(float(c["high"]) for c in window)
                stop = structure_high + self.atr_stop_buffer * atr14[-1]
                distance = stop - price
                if distance > 0:
                    target = price - self.target_r * distance
                    self._entry_bar_time = float(candles[-1]["time"])
                    return Signal(Action.ENTER_SHORT,
                                  f"downtrend pullback to EMA20 (ADX {adx14[-1]:.1f}), bearish confirmation",
                                  stop_loss=stop, take_profit=target)
            return Signal(Action.HOLD, "downtrend, no confirmed pullback entry yet")

        return Signal(Action.HOLD, "no clear trend (EMA stack / ADX not aligned)")
