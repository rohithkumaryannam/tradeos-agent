"""DarkKnight SOL 4H — reconstructed from the public TradingView listing's
description (the actual Pine source is invite-only and was never seen).
The listing describes: a long/short trend-following system on SOLUSDT 4H,
support/resistance breakout entries, momentum + volume confirmation, and
fixed take-profit/stop-loss set from the entry price (no repainting).

Every rule below is a judgment call reconstructing that description, NOT the
verified original logic:
  - Trend filter: EMA50 vs EMA200 — long only above, short only below. The
    listing says "trend-following" but doesn't specify which EMAs; this is
    the same pair used elsewhere in this codebase (btc_trend_pullback).
  - Support/resistance = rolling N-period high/low of the PRIOR candles
    (same Donchian-style construction as donchian_breakout.py) — the listing
    says "support/resistance breakout" without defining the lookback; 20
    candles (80h on 4H) is a reasonable default, not a verified parameter.
  - Momentum confirmation = RSI(14) above/below 50 on the breakout candle —
    the listing says "momentum confirmation" without naming an indicator;
    RSI is the simplest, most common choice.
  - Volume confirmation = breakout candle's volume > 1.2x its 20-candle
    rolling average — mirrors donchian_breakout.py's filter, since the
    listing mentions "volume confirmation" without a threshold.
  - Fixed SL/TP from entry price (not ATR-based) — this matches the listing
    ("fixed take-profit/stop-loss levels set from the actual entry price")
    more literally than other strategies here, which mostly use ATR. Default
    2% stop / 4.4% target approximates the listing's claimed ~2.19 profit
    factor at a 2:1 reward:risk.

None of this has been checked against the original backtest (155 trades,
~67% win rate, profit factor ~2.19, 2022-01-06 to 2026-05-30) since the
source isn't available — treat this as an independent approximation, not a
reproduction, until it's been backtested here on real data.

Select it in config.yaml:
    strategy: { name: darkknight_sol }
Or alongside others:
    strategy: { name: combo, params: { subs: [..., darkknight_sol] } }
"""
from __future__ import annotations

from core.indicators import ema, rsi
from strategies.base import BaseStrategy, Signal, Action

name = "darkknight_sol"


class Strategy(BaseStrategy):
    closed_candles_only = True
    preferred_resolution = "4h"
    supported_resolutions = ("4h",)

    def __init__(self, trend_fast=50, trend_slow=200, sr_period=20,
                 rsi_period=14, volume_mult=1.2, stop_loss_pct=2.0,
                 take_profit_pct=4.4, **kw):
        super().__init__(trend_fast=trend_fast, trend_slow=trend_slow,
                          sr_period=sr_period, rsi_period=rsi_period,
                          volume_mult=volume_mult, stop_loss_pct=stop_loss_pct,
                          take_profit_pct=take_profit_pct, **kw)
        self.trend_fast, self.trend_slow = trend_fast, trend_slow
        self.sr_period, self.rsi_period = sr_period, rsi_period
        self.volume_mult = volume_mult
        self.stop_loss_pct, self.take_profit_pct = stop_loss_pct, take_profit_pct
        self.required_candles = max(trend_slow, sr_period, rsi_period * 2, 20) + 10

    def generate(self, candles, position_side):
        if len(candles) < self.required_candles:
            return Signal(Action.HOLD, "warming up")

        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        closes = [float(c["close"]) for c in candles]
        volumes = [float(c.get("volume") or 0) for c in candles]
        price, vol = closes[-1], volumes[-1]

        ema_fast = ema(closes, self.trend_fast)
        ema_slow = ema(closes, self.trend_slow)
        if ema_fast[-1] is None or ema_slow[-1] is None:
            return Signal(Action.HOLD, "warming up")

        # Fixed SL/TP from entry (already locked in at entry time via the
        # agent's bracket) — this strategy's own exit is just "stay in
        # position"; the exchange bracket handles the real exit.
        if position_side is not None:
            return Signal(Action.HOLD, "in position")

        sr_hi = max(highs[-self.sr_period - 1:-1])
        sr_lo = min(lows[-self.sr_period - 1:-1])

        if vol <= 0:
            return Signal(Action.HOLD, "no volume")
        vol_avg = sum(volumes[-20:]) / 20
        volume_ok = vol > vol_avg * self.volume_mult

        rsi_vals = rsi(closes, self.rsi_period)
        if rsi_vals[-1] is None:
            return Signal(Action.HOLD, "warming up")

        uptrend = ema_fast[-1] > ema_slow[-1]
        downtrend = ema_fast[-1] < ema_slow[-1]

        if uptrend and price > sr_hi and volume_ok and rsi_vals[-1] > 50:
            sl = price * (1 - self.stop_loss_pct / 100)
            tp = price * (1 + self.take_profit_pct / 100)
            return Signal(Action.ENTER_LONG,
                          f"uptrend (EMA{self.trend_fast}>EMA{self.trend_slow}), "
                          f"breakout above {self.sr_period}-period high, "
                          f"RSI {rsi_vals[-1]:.1f} > 50, vol confirmed",
                          stop_loss=sl, take_profit=tp)
        if downtrend and price < sr_lo and volume_ok and rsi_vals[-1] < 50:
            sl = price * (1 + self.stop_loss_pct / 100)
            tp = price * (1 - self.take_profit_pct / 100)
            return Signal(Action.ENTER_SHORT,
                          f"downtrend (EMA{self.trend_fast}<EMA{self.trend_slow}), "
                          f"breakout below {self.sr_period}-period low, "
                          f"RSI {rsi_vals[-1]:.1f} < 50, vol confirmed",
                          stop_loss=sl, take_profit=tp)
        return Signal(Action.HOLD, "no setup")
