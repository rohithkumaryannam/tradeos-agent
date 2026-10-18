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


def adx(candles: list[dict], period: int = 14) -> list[float | None]:
    """Average Directional Index (Wilder's smoothing) from OHLC candles."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n <= period * 2:
        return out
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        ph, pl = float(candles[i - 1]["high"]), float(candles[i - 1]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, down = h - ph, pl - l
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    def _wilder_smooth(series: list[float]) -> list[float]:
        sm = [sum(series[:period])]
        for v in series[period:]:
            sm.append(sm[-1] - sm[-1] / period + v)
        return sm

    tr_s = _wilder_smooth(trs)
    plus_s = _wilder_smooth(plus_dm)
    minus_s = _wilder_smooth(minus_dm)

    dx = []
    for tr_v, p_v, m_v in zip(tr_s, plus_s, minus_s):
        if tr_v == 0:
            dx.append(0.0)
            continue
        plus_di = 100 * p_v / tr_v
        minus_di = 100 * m_v / tr_v
        denom = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denom if denom else 0.0)

    # dx[0] corresponds to candle index `period` (1-based diffs + period warmup)
    if len(dx) < period:
        return out
    adx_val = sum(dx[:period]) / period
    out[period * 2 - 1] = adx_val
    for i, dx_v in enumerate(dx[period:], start=period * 2):
        adx_val = (adx_val * (period - 1) + dx_v) / period
        if i < n:
            out[i] = adx_val
    return out


def closes(candles: list[dict]) -> list[float]:
    return [float(c["close"]) for c in candles]
