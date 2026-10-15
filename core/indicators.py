"""Pure-Python technical indicators. No numpy/pandas needed.

Each function takes a list of floats (or OHLC dicts) and returns a list
aligned to the input, with None for leading values that can't be computed.
"""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    # seed with SMA of first `period` values
    sma = sum(values[:period]) / period
    out[period - 1] = sma
    prev = sma
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    def _rsi(ag, al):
        if al == 0:
            return 100.0
        rs = ag / al
        return 100 - (100 / (1 + rs))
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return out


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    """Average True Range from OHLC candles (keys: high, low, close)."""
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return out
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"]); l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr_val = sum(trs[:period]) / period
    out[period] = atr_val
    for i in range(period + 1, len(candles)):
        atr_val = (atr_val * (period - 1) + trs[i - 1]) / period
        out[i] = atr_val
    return out


def closes(candles: list[dict]) -> list[float]:
    return [float(c["close"]) for c in candles]
