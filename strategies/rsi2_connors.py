"""RSI-2 (Larry Connors) — a well-documented, independently-studied
mean-reversion system (originally for stocks/SPY; reported 76-91% win rates
in published backtests). Adapted here for a 24/7 crypto market (no session
open) and mirrored for shorts (the original is long-only, since shorting
stocks has different mechanics than crypto perps).

Real rules (standard Connors RSI-2, not a reconstruction from a vague
description — this one has a precise, publicly documented methodology):
  - Trend filter: price above trend_ema (default 200-period) -> only take
    longs; below -> only take shorts. Never fight the long-term trend.
  - Entry: RSI(2) <= rsi_oversold (default 10) in an uptrend -> long;
    RSI(2) >= rsi_overbought (default 90) in a downtrend -> short.
  - Exit: price closes back above exit_sma (default 5-period SMA) for a
    long, or back below it for a short — the original "exit on strength"
    rule, not a fixed target.
  - SL/TP here are a backstop only (ATR-based, generous) since the real
    exit is the MA cross — matches donchian_breakout.py's convention.

IMPORTANT caveat found during research: published crypto backtests of
high-win-rate RSI/Bollinger mean-reversion strategies report max drawdowns
of -70% to -155% — rare losses in these systems can be severe even when
most trades win. Win rate alone does not make this safe; check drawdown
before trusting any result.

Select it in config.yaml:
    strategy: { name: rsi2_connors, params: { resolution: "15m" } }
"""
from __future__ import annotations

from core.indicators import ema, rsi, atr
from strategies.base import BaseStrategy, Signal, Action

name = "rsi2_connors"


class Strategy(BaseStrategy):
    closed_candles_only = True

    def __init__(self, resolution="15m", trend_ema=200, rsi_period=2,
                 rsi_oversold=10.0, rsi_overbought=90.0, exit_sma=5,
                 atr_period=14, atr_stop_mult=3.0, fallback_tp_mult=6.0, **kw):
        super().__init__(resolution=resolution, trend_ema=trend_ema, rsi_period=rsi_period,
                          rsi_oversold=rsi_oversold, rsi_overbought=rsi_overbought,
                          exit_sma=exit_sma, atr_period=atr_period,
                          atr_stop_mult=atr_stop_mult, fallback_tp_mult=fallback_tp_mult, **kw)
        self.preferred_resolution = resolution
        self.supported_resolutions = (resolution,)
        self.trend_ema, self.rsi_period = trend_ema, rsi_period
        self.rsi_oversold, self.rsi_overbought = rsi_oversold, rsi_overbought
        self.exit_sma = exit_sma
        self.atr_period, self.atr_stop_mult = atr_period, atr_stop_mult
        self.fallback_tp_mult = fallback_tp_mult
        self.required_candles = max(trend_ema, exit_sma, atr_period) + 10

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        closes = [float(c["close"]) for c in candles]
        price = closes[-1]
        sma_exit = sum(closes[-self.exit_sma:]) / self.exit_sma

        if position_side == "long" and price > sma_exit:
            return Signal(Action.EXIT, f"closed back above {self.exit_sma}-period SMA")
        if position_side == "short" and price < sma_exit:
            return Signal(Action.EXIT, f"closed back below {self.exit_sma}-period SMA")
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        trend = ema(closes, self.trend_ema)
        rsi_vals = rsi(closes, self.rsi_period)
        atr_vals = atr(candles, self.atr_period)
        if trend[-1] is None or rsi_vals[-1] is None or atr_vals[-1] is None:
            return Signal(Action.HOLD, "warming up")

        stop_dist = self.atr_stop_mult * atr_vals[-1]
        uptrend = price > trend[-1]
        downtrend = price < trend[-1]

        if uptrend and rsi_vals[-1] <= self.rsi_oversold:
            sl = price - stop_dist
            tp = price + stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_LONG,
                          f"uptrend (price > EMA{self.trend_ema}), RSI({self.rsi_period})={rsi_vals[-1]:.1f} oversold",
                          stop_loss=sl, take_profit=tp)
        if downtrend and rsi_vals[-1] >= self.rsi_overbought:
            sl = price + stop_dist
            tp = price - stop_dist * self.fallback_tp_mult
            return Signal(Action.ENTER_SHORT,
                          f"downtrend (price < EMA{self.trend_ema}), RSI({self.rsi_period})={rsi_vals[-1]:.1f} overbought",
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "no setup")
