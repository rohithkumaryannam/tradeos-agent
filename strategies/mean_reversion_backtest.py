#!/usr/bin/env python3
"""
BTCUSD / ETHUSD 15-minute Regime-Filtered Mean-Reversion Backtester
===================================================================

Research-only. This script does not connect to an exchange or place orders.

Strategy baseline
------------------
Indicators:
  - Bollinger Bands: 20 bars, 2 standard deviations
  - RSI: 14 bars
  - ADX: 14 bars
  - ATR: 14 bars

Long signal on a completed candle:
  - ADX < configured threshold (default 20)
  - Close below lower Bollinger Band
  - RSI below configured threshold (default 30)
  - Middle Bollinger Band is above the close

Short signal is the inverse:
  - ADX < threshold
  - Close above upper Bollinger Band
  - RSI above configured threshold (default 70)
  - Middle Bollinger Band is below the close

Execution and exits:
  - Signal on completed bar t; enter at bar t+1 open, with adverse slippage.
  - Target is the middle Bollinger Band calculated on the signal bar (fixed at entry).
  - Stop is ATR-based from the actual entry price (default 1.5 * ATR).
  - If stop and target are touched in the same candle, stop is assumed first.
  - Positions are closed after the maximum holding period or at end of data.
  - One position at a time; no pyramiding or averaging down.

Risk:
  - Default risk budget is 0.25% of equity per trade.
  - Daily loss stop defaults to 1% of UTC-day starting equity.
  - Stop taking new trades after 3 consecutive losing trades in a UTC day.
  - Position sizing includes an estimated fee/slippage buffer.
  - Quantity is rounded down to the configured step and capped by notional leverage.

CSV input:
  Required columns: timestamp, open, high, low, close, volume
  Optional column: funding_rate
  Positive funding_rate is modeled as longs paying and shorts receiving at each row
  where a nonzero rate is present. Supply only actual funding-event rows, not a
  rate forward-filled across every candle.

Install:
  pip install pandas numpy

Run:
  python mean_reversion_backtest.py --data BTCUSD_15m.csv --out results_btc
  python mean_reversion_backtest.py --data ETHUSD_15m.csv --out results_eth

Optional overrides:
  --initial-equity 10000
  --risk 0.0025
  --fee 0.0005
  --slippage 0.0002
  --adx-max 20
  --rsi-long 30
  --rsi-short 70
  --atr-stop 1.5
  --target-r  # not used: target is middle Bollinger Band

Important limitations:
  - This is a baseline simulator, not proof of an edge.
  - Contract multiplier, tick size, quantity step, fees, funding convention and
    exchange-specific P&L must be verified for the actual BTCUSD/ETHUSD product.
  - OHLC data cannot reveal the exact order of intrabar prices. Same-bar stop/target
    collisions are handled conservatively, but real fills may differ.
  - Train/validation/out-of-sample splits are chronological and each segment starts
    flat. This is simple split evaluation, not a full walk-forward optimization.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Config:
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.0025
    taker_fee_rate: float = 0.0005
    slippage_rate: float = 0.0002
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_long_threshold: float = 30.0
    rsi_short_threshold: float = 70.0
    adx_period: int = 14
    adx_max: float = 20.0
    atr_period: int = 14
    atr_stop_multiple: float = 1.5
    max_holding_bars: int = 32
    max_daily_loss_pct: float = 0.01
    max_consecutive_losses: int = 3
    max_notional_leverage: float = 3.0
    min_quantity: float = 0.001
    quantity_step: float = 0.001
    train_fraction: float = 0.60
    validation_fraction: float = 0.20


def calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Explicit edge handling
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return rsi


def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def calculate_adx(df: pd.DataFrame, period: int) -> pd.Series:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_smoothed = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_smoothed = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_smoothed / atr.replace(0, np.nan)
    minus_di = 100 * minus_smoothed / atr.replace(0, np.nan)
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["bb_mid"] = out["close"].rolling(cfg.bb_period, min_periods=cfg.bb_period).mean()
    bb_std = out["close"].rolling(cfg.bb_period, min_periods=cfg.bb_period).std(ddof=0)
    out["bb_upper"] = out["bb_mid"] + cfg.bb_std * bb_std
    out["bb_lower"] = out["bb_mid"] - cfg.bb_std * bb_std
    out["rsi"] = calculate_rsi(out["close"], cfg.rsi_period)
    out["atr"] = calculate_atr(out, cfg.atr_period)
    out["adx"] = calculate_adx(out, cfg.adx_period)

    long_signal = (
        (out["adx"] < cfg.adx_max)
        & (out["close"] < out["bb_lower"])
        & (out["rsi"] < cfg.rsi_long_threshold)
        & (out["bb_mid"] > out["close"])
    )
    short_signal = (
        (out["adx"] < cfg.adx_max)
        & (out["close"] > out["bb_upper"])
        & (out["rsi"] > cfg.rsi_short_threshold)
        & (out["bb_mid"] < out["close"])
    )
    out["signal"] = 0
    out.loc[long_signal, "signal"] = 1
    out.loc[short_signal, "signal"] = -1
    return out


def load_data(path: Path, cfg: Config) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if df["timestamp"].isna().any():
        raise ValueError("Could not parse one or more timestamps.")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("OHLCV columns contain missing or non-numeric values.")
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (df["high"] < df["low"]).any():
        raise ValueError("Found candle with high < low.")
    if (df["high"] < df[["open", "close"]].max(axis=1)).any():
        raise ValueError("Found candle whose high is below open or close.")
    if (df["low"] > df[["open", "close"]].min(axis=1)).any():
        raise ValueError("Found candle whose low is above open or close.")

    if "funding_rate" in df.columns:
        df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce").fillna(0.0)
    else:
        df["funding_rate"] = 0.0
        print("WARNING: no funding_rate column; funding costs will be omitted.")

    # Warn about missing 15-minute bars without silently fabricating candles.
    diffs = df["timestamp"].diff().dropna()
    missing_intervals = int((diffs > pd.Timedelta(minutes=15)).sum())
    if missing_intervals:
        print(f"WARNING: found {missing_intervals} gaps longer than 15 minutes.")
    if len(df) < 1000:
        print("WARNING: fewer than 1,000 candles; statistical evidence will be weak.")
    return add_indicators(df, cfg)


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return float(np.floor(value / step + 1e-12) * step)


def apply_slippage(price: float, side: int, is_entry: bool, slip: float) -> float:
    # Adverse execution: long entry higher / exit lower; short entry lower / exit higher.
    adverse_up = (side == 1 and is_entry) or (side == -1 and not is_entry)
    return price * (1 + slip if adverse_up else 1 - slip)


def metrics(trades: pd.DataFrame, curve: pd.DataFrame, initial_equity: float) -> dict:
    if curve.empty:
        final_equity = initial_equity
        max_dd = 0.0
    else:
        final_equity = float(curve["equity"].iloc[-1])
        peaks = curve["equity"].cummax()
        drawdowns = curve["equity"] / peaks - 1.0
        max_dd = float(drawdowns.min() * 100)

    if trades.empty:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "profit_factor": None,
            "expectancy_usd_per_trade": None,
            "net_pnl_usd": round(final_equity - initial_equity, 4),
            "return_pct": round((final_equity / initial_equity - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "average_r_multiple": None,
        }

    pnl = trades["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_wins = float(wins.sum())
    gross_losses = float(-losses.sum())
    pf = gross_wins / gross_losses if gross_losses > 0 else (float("inf") if gross_wins > 0 else None)
    return {
        "trade_count": int(len(trades)),
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 3),
        "profit_factor": round(pf, 4) if pf is not None and np.isfinite(pf) else ("Infinity" if pf == float("inf") else None),
        "expectancy_usd_per_trade": round(float(pnl.mean()), 4),
        "net_pnl_usd": round(float(pnl.sum()), 4),
        "return_pct": round((final_equity / initial_equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "average_r_multiple": round(float(trades["r_multiple"].mean()), 4),
        "average_winner_usd": round(float(wins.mean()), 4) if len(wins) else None,
        "average_loser_usd": round(float(losses.mean()), 4) if len(losses) else None,
    }


def run_backtest(df: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.reset_index(drop=True).copy()
    equity = cfg.initial_equity
    trades: list[dict] = []
    curve: list[dict] = []
    pos: Optional[dict] = None
    active_day = None
    day_start_equity = equity
    consecutive_losses = 0
    daily_halt = False

    # Indicators have their own rolling warm-up; 250 bars avoids early unstable values.
    warmup = max(250, cfg.bb_period + 5)
    for i in range(warmup, len(df)):
        bar = df.iloc[i]
        timestamp = bar["timestamp"]
        day = timestamp.strftime("%Y-%m-%d")

        if day != active_day:
            active_day = day
            day_start_equity = equity
            consecutive_losses = 0
            daily_halt = False

        # Manage current position. Stops/targets are checked against bars after entry bar.
        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            stop_hit = (bar["low"] <= pos["stop"]) if side == 1 else (bar["high"] >= pos["stop"])
            target_hit = (bar["high"] >= pos["target"]) if side == 1 else (bar["low"] <= pos["target"])
            exit_raw = None
            exit_reason = None

            if stop_hit and target_hit:
                exit_raw, exit_reason = pos["stop"], "stop_and_target_same_bar_stop_first"
            elif stop_hit:
                # If the bar opens through the stop, model the worse opening price.
                exit_raw = min(float(bar["open"]), pos["stop"]) if side == 1 else max(float(bar["open"]), pos["stop"])
                exit_reason = "stop"
            elif target_hit:
                # Target is a limit-like fill; if the bar opens through it, use open.
                exit_raw = max(float(bar["open"]), pos["target"]) if side == 1 else min(float(bar["open"]), pos["target"])
                exit_reason = "middle_band_target"
            elif i - pos["entry_i"] >= cfg.max_holding_bars:
                exit_raw, exit_reason = float(bar["close"]), "time_exit"

            funding_rate = float(bar.get("funding_rate", 0.0))
            if funding_rate:
                # Positive rate: long pays, short receives. Only valid for event rows.
                pos["funding_pnl"] += -side * pos["qty"] * float(bar["close"]) * funding_rate

            if exit_raw is not None:
                exit_exec = apply_slippage(float(exit_raw), side, False, cfg.slippage_rate)
                exit_notional = pos["qty"] * exit_exec
                exit_fee = exit_notional * cfg.taker_fee_rate
                gross_pnl = side * pos["qty"] * (exit_exec - pos["entry_exec"])
                net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_pnl"]
                equity += net_pnl
                r_mult = net_pnl / pos["risk_dollars"] if pos["risk_dollars"] else 0.0

                trades.append({
                    "side": "LONG" if side == 1 else "SHORT",
                    "signal_time": pos["signal_time"],
                    "entry_time": pos["entry_time"],
                    "exit_time": timestamp,
                    "entry_price_raw": pos["entry_raw"],
                    "entry_price_executed": pos["entry_exec"],
                    "exit_price_raw": float(exit_raw),
                    "exit_price_executed": exit_exec,
                    "quantity": pos["qty"],
                    "stop_price": pos["stop"],
                    "target_middle_band": pos["target"],
                    "exit_reason": exit_reason,
                    "gross_pnl": gross_pnl,
                    "entry_fee": pos["entry_fee"],
                    "exit_fee": exit_fee,
                    "funding_pnl": pos["funding_pnl"],
                    "net_pnl": net_pnl,
                    "risk_budget_usd": pos["risk_dollars"],
                    "r_multiple": r_mult,
                    "equity_after": equity,
                })

                if net_pnl < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0
                if day_start_equity > 0 and (day_start_equity - equity) / day_start_equity >= cfg.max_daily_loss_pct:
                    daily_halt = True
                if consecutive_losses >= cfg.max_consecutive_losses:
                    daily_halt = True
                pos = None

        # If flat, consider the prior completed candle's signal at this bar's open.
        if pos is None and not daily_halt and equity > 0:
            sig_bar = df.iloc[i - 1]
            side = int(sig_bar["signal"])
            if side != 0 and np.isfinite(sig_bar["atr"]) and np.isfinite(sig_bar["bb_mid"]):
                entry_raw = float(bar["open"])
                atr = float(sig_bar["atr"])
                stop = entry_raw - side * cfg.atr_stop_multiple * atr
                target = float(sig_bar["bb_mid"])

                # A valid mean-reversion target must be on the profitable side of entry.
                if (side == 1 and target <= entry_raw) or (side == -1 and target >= entry_raw):
                    curve.append({"timestamp": timestamp, "equity": equity})
                    continue
                stop_distance = abs(entry_raw - stop)
                if stop_distance <= 0:
                    curve.append({"timestamp": timestamp, "equity": equity})
                    continue

                risk_dollars = equity * cfg.risk_per_trade
                estimated_round_trip_cost_per_unit = entry_raw * 2 * (cfg.taker_fee_rate + cfg.slippage_rate)
                qty = floor_step(
                    risk_dollars / (stop_distance + estimated_round_trip_cost_per_unit),
                    cfg.quantity_step,
                )
                qty_cap = floor_step(equity * cfg.max_notional_leverage / entry_raw, cfg.quantity_step)
                qty = min(qty, qty_cap)
                if qty < cfg.min_quantity:
                    curve.append({"timestamp": timestamp, "equity": equity})
                    continue

                entry_exec = apply_slippage(entry_raw, side, True, cfg.slippage_rate)
                entry_fee = qty * entry_exec * cfg.taker_fee_rate
                estimated_stop_loss = (
                    qty * (stop_distance + entry_raw * 2 * cfg.slippage_rate)
                    + entry_fee
                    + qty * stop * cfg.taker_fee_rate
                )
                if estimated_stop_loss > risk_dollars * 1.10:
                    curve.append({"timestamp": timestamp, "equity": equity})
                    continue

                pos = {
                    "side": side,
                    "entry_i": i,
                    "signal_time": sig_bar["timestamp"],
                    "entry_time": timestamp,
                    "entry_raw": entry_raw,
                    "entry_exec": entry_exec,
                    "stop": float(stop),
                    "target": target,
                    "qty": float(qty),
                    "entry_fee": float(entry_fee),
                    "funding_pnl": 0.0,
                    "risk_dollars": float(risk_dollars),
                }

        curve.append({"timestamp": timestamp, "equity": equity})

    # Force-close at the final close if a position remains.
    if pos is not None and len(df):
        bar = df.iloc[-1]
        side = pos["side"]
        exit_raw = float(bar["close"])
        exit_exec = apply_slippage(exit_raw, side, False, cfg.slippage_rate)
        exit_fee = pos["qty"] * exit_exec * cfg.taker_fee_rate
        gross_pnl = side * pos["qty"] * (exit_exec - pos["entry_exec"])
        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_pnl"]
        equity += net_pnl
        trades.append({
            "side": "LONG" if side == 1 else "SHORT",
            "signal_time": pos["signal_time"],
            "entry_time": pos["entry_time"],
            "exit_time": bar["timestamp"],
            "entry_price_raw": pos["entry_raw"],
            "entry_price_executed": pos["entry_exec"],
            "exit_price_raw": exit_raw,
            "exit_price_executed": exit_exec,
            "quantity": pos["qty"],
            "stop_price": pos["stop"],
            "target_middle_band": pos["target"],
            "exit_reason": "end_of_data",
            "gross_pnl": gross_pnl,
            "entry_fee": pos["entry_fee"],
            "exit_fee": exit_fee,
            "funding_pnl": pos["funding_pnl"],
            "net_pnl": net_pnl,
            "risk_budget_usd": pos["risk_dollars"],
            "r_multiple": net_pnl / pos["risk_dollars"] if pos["risk_dollars"] else 0.0,
            "equity_after": equity,
        })
        if curve:
            curve[-1]["equity"] = equity

    return pd.DataFrame(trades), pd.DataFrame(curve)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest regime-filtered mean reversion on 15m BTCUSD/ETHUSD data.")
    parser.add_argument("--data", required=True, help="CSV with timestamp,open,high,low,close,volume.")
    parser.add_argument("--out", default="mean_reversion_results", help="Output directory.")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.0025, help="Risk fraction per trade, e.g. 0.0025 = 0.25%%.")
    parser.add_argument("--fee", type=float, default=0.0005, help="Taker fee per side as decimal.")
    parser.add_argument("--slippage", type=float, default=0.0002, help="Adverse slippage per side as decimal.")
    parser.add_argument("--adx-max", type=float, default=20.0)
    parser.add_argument("--rsi-long", type=float, default=30.0)
    parser.add_argument("--rsi-short", type=float, default=70.0)
    parser.add_argument("--atr-stop", type=float, default=1.5)
    parser.add_argument("--max-hold-bars", type=int, default=32)
    parser.add_argument("--min-qty", type=float, default=0.001)
    parser.add_argument("--qty-step", type=float, default=0.001)
    args = parser.parse_args()

    cfg = Config(
        initial_equity=args.initial_equity,
        risk_per_trade=args.risk,
        taker_fee_rate=args.fee,
        slippage_rate=args.slippage,
        adx_max=args.adx_max,
        rsi_long_threshold=args.rsi_long,
        rsi_short_threshold=args.rsi_short,
        atr_stop_multiple=args.atr_stop,
        max_holding_bars=args.max_hold_bars,
        min_quantity=args.min_qty,
        quantity_step=args.qty_step,
    )
    if not (0 < cfg.risk_per_trade <= 0.01):
        raise ValueError("--risk should be > 0 and <= 0.01 (1%) for this research baseline.")
    if cfg.taker_fee_rate < 0 or cfg.slippage_rate < 0:
        raise ValueError("Fee and slippage rates cannot be negative.")

    data_path = Path(args.data)
    df = load_data(data_path, cfg)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    n = len(df)
    train_end = int(n * cfg.train_fraction)
    val_end = int(n * (cfg.train_fraction + cfg.validation_fraction))
    splits = {
        "train": df.iloc[:train_end].reset_index(drop=True),
        "validation": df.iloc[train_end:val_end].reset_index(drop=True),
        "out_of_sample": df.iloc[val_end:].reset_index(drop=True),
    }

    report = {
        "strategy": "Regime-Filtered Mean Reversion v1",
        "data_file": str(data_path),
        "warning": "No profitability or win-rate guarantee. Verify contract multiplier, costs, funding events, and execution.",
        "config": asdict(cfg),
        "data": {
            "rows": int(n),
            "start_utc": str(df["timestamp"].min()),
            "end_utc": str(df["timestamp"].max()),
            "split_method": "chronological 60% train / 20% validation / 20% out-of-sample; each segment starts flat",
        },
        "splits": {},
    }

    for name, segment in splits.items():
        trades, curve = run_backtest(segment, cfg)
        trades_file = outdir / f"{name}_trades.csv"
        equity_file = outdir / f"{name}_equity.csv"
        trades.to_csv(trades_file, index=False)
        curve.to_csv(equity_file, index=False)
        split_metrics = metrics(trades, curve, cfg.initial_equity)
        split_metrics["trades_file"] = trades_file.name
        split_metrics["equity_file"] = equity_file.name
        report["splits"][name] = split_metrics

    report_file = outdir / "report.json"
    report_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nFiles saved to: {outdir.resolve()}")
    print("Review report.json and out_of_sample_trades.csv first.")
    print("Do not use live until out-of-sample results, exchange contract sizing, fees, funding, and fills are independently verified.")


if __name__ == "__main__":
    main()
