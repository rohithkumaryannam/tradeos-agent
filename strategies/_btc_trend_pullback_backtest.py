#!/usr/bin/env python3
"""
BTCUSD 15-minute Trend-Pullback Backtester
==========================================

Research-only backtester. It does NOT connect to an exchange or place orders.

Input CSV required columns:
    timestamp, open, high, low, close, volume

Timestamp should be parseable by pandas. Use UTC timestamps where possible.
Optional columns:
    funding_rate
If funding_rate is supplied, it is interpreted as the funding rate (decimal, e.g.
0.0001 = 0.01%) paid by a long at each row's timestamp; shorts receive/pay the
opposite. The script charges funding on bars whose timestamp is a funding event
according to the dataset. Do not supply a funding series that has been forward-
filled to every bar unless that is intentional.

Install:
    pip install pandas numpy

Run:
    python btc_trend_pullback_backtest.py --data BTCUSD_15m.csv --out results

Assumptions:
- Indicators and signals use completed candles only.
- Signal at bar t executes at bar t+1 open.
- Stops/targets are checked using OHLC bars. If stop and target are both touched
  in the same candle, the stop is assumed to have been hit first (conservative).
- If price gaps beyond a stop, fill at the bar open when that is worse.
- Market-order entry and exit fees are modeled using taker_fee_rate.
- Slippage is an adverse percentage of notional on each entry/exit.
- P&L and sizing assume a linear USD-quoted contract: P&L = quantity * price change.
  Verify your exchange contract multiplier, tick/lot size, fee schedule and funding
  convention before relying on these results.
- Parameter selection is NOT automated. This script reports a chronological
  60/20/20 split, but does not tune on the test period.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Config:
    initial_equity: float = 10_000.0
    risk_per_trade: float = 0.0025       # 0.25% equity risk budget
    taker_fee_rate: float = 0.0005       # 0.05% per side; verify current account fee
    slippage_rate: float = 0.0002        # 0.02% adverse slippage per side; stress-test
    target_r: float = 2.0
    atr_stop_buffer: float = 0.2
    adx_min: float = 20.0
    max_holding_bars: int = 32           # 8 hours on 15m bars
    max_daily_loss_pct: float = 0.01    # stop new entries after 1% daily loss
    max_consecutive_losses: int = 3
    max_notional_leverage: float = 3.0  # conservative notional cap; not a target
    min_quantity: float = 0.001
    quantity_step: float = 0.001
    train_fraction: float = 0.60
    validation_fraction: float = 0.20


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    atr_safe = df["atr14"].replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx14"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    df["volume_sma20"] = df["volume"].rolling(20, min_periods=20).mean()

    # Pullback is evaluated on previous completed candle; confirmation on current close.
    prev = df.shift(1)
    long_trend = (
        (df["close"] > df["ema200"])
        & (df["ema50"] > df["ema200"])
        & (df["ema20"] > df["ema50"])
        & (df["adx14"] >= 20.0)
    )
    short_trend = (
        (df["close"] < df["ema200"])
        & (df["ema50"] < df["ema200"])
        & (df["ema20"] < df["ema50"])
        & (df["adx14"] >= 20.0)
    )
    long_pullback = prev["low"] <= prev["ema20"] + 0.1 * prev["atr14"]
    short_pullback = prev["high"] >= prev["ema20"] - 0.1 * prev["atr14"]
    bullish_confirmation = (df["close"] > df["open"]) & (df["close"] > df["ema20"])
    bearish_confirmation = (df["close"] < df["open"]) & (df["close"] < df["ema20"])

    df["signal"] = 0
    df.loc[long_trend & long_pullback & bullish_confirmation, "signal"] = 1
    df.loc[short_trend & short_pullback & bearish_confirmation, "signal"] = -1
    return df


def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if df["timestamp"].isna().any():
        raise ValueError("Some timestamps could not be parsed.")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("OHLCV columns contain missing/non-numeric values.")
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if len(df) < 1000:
        print("WARNING: Fewer than 1,000 candles; results may be statistically weak.")
    if (df["high"] < df["low"]).any():
        raise ValueError("Found candle with high < low.")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Prices must be positive.")
    if "funding_rate" in df.columns:
        df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce").fillna(0.0)
    else:
        df["funding_rate"] = 0.0
        print("WARNING: No funding_rate column found; funding costs are omitted.")
    return add_indicators(df)


def floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return np.floor(value / step) * step


def adverse_price(price: float, side: int, is_entry: bool, slip: float) -> float:
    # side = +1 long, -1 short
    # Long entry buys higher; long exit sells lower. Short entry sells lower; short exit buys higher.
    adverse_up = (side == 1 and is_entry) or (side == -1 and not is_entry)
    return price * (1 + slip if adverse_up else 1 - slip)


def compute_metrics(trades: pd.DataFrame, equity_curve: pd.DataFrame, start_equity: float) -> dict:
    if trades.empty:
        return {
            "trades": 0, "win_rate_pct": None, "profit_factor": None,
            "expectancy_usd": None, "net_pnl_usd": 0.0, "return_pct": 0.0,
            "max_drawdown_pct": 0.0, "avg_win_usd": None, "avg_loss_usd": None,
        }
    pnl = trades["net_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    end_equity = float(equity_curve["equity"].iloc[-1]) if not equity_curve.empty else start_equity
    peak = equity_curve["equity"].cummax()
    dd = (equity_curve["equity"] / peak - 1.0) * 100
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else None)
    return {
        "trades": int(len(trades)),
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 3),
        "profit_factor": round(float(pf), 4) if pf is not None and np.isfinite(pf) else ("Infinity" if pf == float("inf") else None),
        "expectancy_usd": round(float(pnl.mean()), 4),
        "net_pnl_usd": round(float(pnl.sum()), 4),
        "return_pct": round((end_equity / start_equity - 1) * 100, 4),
        "max_drawdown_pct": round(float(dd.min()), 4) if len(dd) else 0.0,
        "avg_win_usd": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss_usd": round(float(losses.mean()), 4) if len(losses) else None,
    }


def backtest(df: pd.DataFrame, cfg: Config, start_equity: Optional[float] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = df.reset_index(drop=True).copy()
    equity = float(cfg.initial_equity if start_equity is None else start_equity)
    trades: list[dict] = []
    curve: list[dict] = []
    position = None
    daily_start_equity = equity
    active_day = None
    consecutive_losses = 0
    halted_day = False

    warmup = 220
    for i in range(warmup, len(data)):
        row = data.iloc[i]
        ts = row["timestamp"]
        day = ts.strftime("%Y-%m-%d")
        if active_day != day:
            active_day = day
            daily_start_equity = equity
            consecutive_losses = 0
            halted_day = False

        # Manage an open position using this bar's OHLC.
        if position is not None:
            side = position["side"]
            exit_price = None
            exit_reason = None
            # On the entry bar, do not check the stop/target: entry occurs at this bar's open
            # and OHLC after the open is not sequenced. Subsequent bars are checked normally.
            if i > position["entry_i"]:
                if side == 1:
                    stop_hit = row["low"] <= position["stop"]
                    target_hit = row["high"] >= position["target"]
                    if stop_hit and target_hit:
                        exit_price, exit_reason = position["stop"], "stop_and_target_same_bar_stop_first"
                    elif stop_hit:
                        exit_price, exit_reason = min(float(row["open"]), position["stop"]), "stop"
                    elif target_hit:
                        exit_price, exit_reason = max(float(row["open"]), position["target"]), "target"
                else:
                    stop_hit = row["high"] >= position["stop"]
                    target_hit = row["low"] <= position["target"]
                    if stop_hit and target_hit:
                        exit_price, exit_reason = position["stop"], "stop_and_target_same_bar_stop_first"
                    elif stop_hit:
                        exit_price, exit_reason = max(float(row["open"]), position["stop"]), "stop"
                    elif target_hit:
                        exit_price, exit_reason = min(float(row["open"]), position["target"]), "target"

                if exit_price is None and (i - position["entry_i"]) >= cfg.max_holding_bars:
                    # Exit on this bar close as a conservative, explicit time-exit approximation.
                    exit_price, exit_reason = float(row["close"]), "time_exit"

                # Funding is applied if this bar has a nonzero event rate.
                rate = float(row.get("funding_rate", 0.0))
                if rate:
                    # Positive rate means longs pay, shorts receive.
                    funding_cashflow = -side * position["qty"] * float(row["close"]) * rate
                    position["funding"] += funding_cashflow

            if exit_price is not None:
                raw_exit = float(exit_price)
                executed_exit = adverse_price(raw_exit, side, False, cfg.slippage_rate)
                exit_notional = position["qty"] * executed_exit
                exit_fee = exit_notional * cfg.taker_fee_rate
                gross_pnl = side * position["qty"] * (executed_exit - position["entry_exec"])
                net_pnl = gross_pnl - position["entry_fee"] - exit_fee + position["funding"]
                equity += net_pnl
                risk_dollars = position["risk_dollars"]
                r_multiple = net_pnl / risk_dollars if risk_dollars > 0 else 0.0
                trade = {
                    "side": "LONG" if side == 1 else "SHORT",
                    "entry_time": position["entry_time"],
                    "exit_time": ts,
                    "entry_signal_time": position["signal_time"],
                    "entry_price_raw": position["entry_raw"],
                    "entry_price_executed": position["entry_exec"],
                    "exit_price_raw": raw_exit,
                    "exit_price_executed": executed_exit,
                    "quantity": position["qty"],
                    "stop_price": position["stop"],
                    "target_price": position["target"],
                    "exit_reason": exit_reason,
                    "gross_pnl": gross_pnl,
                    "entry_fee": position["entry_fee"],
                    "exit_fee": exit_fee,
                    "funding_pnl": position["funding"],
                    "net_pnl": net_pnl,
                    "risk_dollars": risk_dollars,
                    "r_multiple": r_multiple,
                    "equity_after": equity,
                }
                trades.append(trade)
                if net_pnl < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0
                if (daily_start_equity - equity) / daily_start_equity >= cfg.max_daily_loss_pct:
                    halted_day = True
                if consecutive_losses >= cfg.max_consecutive_losses:
                    halted_day = True
                position = None

        # Daily risk gate is checked before new entry.
        if position is None and not halted_day and equity > 0:
            # Signal from previous completed candle; entry at this bar's open.
            sig = int(data.iloc[i - 1]["signal"])
            signal_row = data.iloc[i - 1]
            if sig != 0 and np.isfinite(signal_row["atr14"]):
                entry_raw = float(row["open"])
                side = sig
                # Structure stop based on signal candle and preceding three completed candles.
                window = data.iloc[max(0, i - 4):i]
                if side == 1:
                    structure_extreme = float(window["low"].min())
                    stop = structure_extreme - cfg.atr_stop_buffer * float(signal_row["atr14"])
                    distance = entry_raw - stop
                else:
                    structure_extreme = float(window["high"].max())
                    stop = structure_extreme + cfg.atr_stop_buffer * float(signal_row["atr14"])
                    distance = stop - entry_raw

                if distance <= 0 or not np.isfinite(distance):
                    curve.append({"timestamp": ts, "equity": equity})
                    continue

                risk_dollars = equity * cfg.risk_per_trade
                # Estimate round-trip costs conservatively in sizing buffer.
                cost_per_unit = distance + entry_raw * (2 * (cfg.taker_fee_rate + cfg.slippage_rate))
                qty = floor_step(risk_dollars / cost_per_unit, cfg.quantity_step)
                max_qty = floor_step((equity * cfg.max_notional_leverage) / entry_raw, cfg.quantity_step)
                qty = min(qty, max_qty)
                if qty < cfg.min_quantity:
                    curve.append({"timestamp": ts, "equity": equity})
                    continue

                entry_exec = adverse_price(entry_raw, side, True, cfg.slippage_rate)
                entry_fee = qty * entry_exec * cfg.taker_fee_rate
                target = entry_raw + side * cfg.target_r * distance
                # Risk gate after estimated costs; skip if minimum order cannot fit.
                estimated_loss = qty * (distance + entry_raw * 2 * cfg.slippage_rate) + entry_fee + qty * stop * cfg.taker_fee_rate
                if estimated_loss > equity * cfg.risk_per_trade * 1.10:
                    curve.append({"timestamp": ts, "equity": equity})
                    continue
                position = {
                    "side": side,
                    "entry_i": i,
                    "entry_time": ts,
                    "signal_time": signal_row["timestamp"],
                    "entry_raw": entry_raw,
                    "entry_exec": entry_exec,
                    "stop": float(stop),
                    "target": float(target),
                    "qty": float(qty),
                    "entry_fee": float(entry_fee),
                    "funding": 0.0,
                    "risk_dollars": float(risk_dollars),
                }

        curve.append({"timestamp": ts, "equity": equity})

    # Force-close remaining position at last close.
    if position is not None and len(data):
        row = data.iloc[-1]
        side = position["side"]
        raw_exit = float(row["close"])
        executed_exit = adverse_price(raw_exit, side, False, cfg.slippage_rate)
        exit_fee = position["qty"] * executed_exit * cfg.taker_fee_rate
        gross_pnl = side * position["qty"] * (executed_exit - position["entry_exec"])
        net_pnl = gross_pnl - position["entry_fee"] - exit_fee + position["funding"]
        equity += net_pnl
        trades.append({
            "side": "LONG" if side == 1 else "SHORT",
            "entry_time": position["entry_time"], "exit_time": row["timestamp"],
            "entry_signal_time": position["signal_time"],
            "entry_price_raw": position["entry_raw"], "entry_price_executed": position["entry_exec"],
            "exit_price_raw": raw_exit, "exit_price_executed": executed_exit,
            "quantity": position["qty"], "stop_price": position["stop"], "target_price": position["target"],
            "exit_reason": "end_of_data", "gross_pnl": gross_pnl,
            "entry_fee": position["entry_fee"], "exit_fee": exit_fee,
            "funding_pnl": position["funding"], "net_pnl": net_pnl,
            "risk_dollars": position["risk_dollars"],
            "r_multiple": net_pnl / position["risk_dollars"] if position["risk_dollars"] else 0.0,
            "equity_after": equity,
        })
        if curve:
            curve[-1]["equity"] = equity

    return pd.DataFrame(trades), pd.DataFrame(curve)


def split_and_run(df: pd.DataFrame, cfg: Config, outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    n = len(df)
    train_end = int(n * cfg.train_fraction)
    val_end = int(n * (cfg.train_fraction + cfg.validation_fraction))
    # Indicators are precomputed causally across the dataset. Each split begins flat.
    # Warm-up candles are retained inside each split for indicator stability, but
    # trade evaluation starts after the backtest's 220-bar warm-up.
    segments = {
        "train": df.iloc[:train_end].reset_index(drop=True),
        "validation": df.iloc[train_end:val_end].reset_index(drop=True),
        "out_of_sample": df.iloc[val_end:].reset_index(drop=True),
    }
    report = {
        "strategy": "BTC Trend-Pullback v1",
        "warning": "Research only; not evidence of future profitability. Verify contract specs and costs.",
        "config": asdict(cfg),
        "data": {
            "rows": int(n),
            "start": str(df["timestamp"].min()),
            "end": str(df["timestamp"].max()),
            "train_rows": len(segments["train"]),
            "validation_rows": len(segments["validation"]),
            "out_of_sample_rows": len(segments["out_of_sample"]),
        },
        "splits": {},
    }
    for name, seg in segments.items():
        if len(seg) <= 220:
            report["splits"][name] = {"error": "Not enough rows after indicator warm-up."}
            continue
        trades, curve = backtest(seg, cfg)
        trades_path = outdir / f"{name}_trades.csv"
        curve_path = outdir / f"{name}_equity.csv"
        trades.to_csv(trades_path, index=False)
        curve.to_csv(curve_path, index=False)
        metrics = compute_metrics(trades, curve, cfg.initial_equity)
        metrics["trades_csv"] = trades_path.name
        metrics["equity_csv"] = curve_path.name
        report["splits"][name] = metrics

    report_path = outdir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest BTCUSD 15m trend-pullback rules.")
    parser.add_argument("--data", required=True, help="Path to OHLCV CSV (timestamp,open,high,low,close,volume).")
    parser.add_argument("--out", default="btc_backtest_results", help="Output directory.")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk", type=float, default=0.0025, help="Risk per trade as decimal, e.g. 0.0025 = 0.25%%.")
    parser.add_argument("--fee", type=float, default=0.0005, help="Taker fee per side as decimal.")
    parser.add_argument("--slippage", type=float, default=0.0002, help="Adverse slippage per side as decimal.")
    parser.add_argument("--min-qty", type=float, default=0.001)
    parser.add_argument("--qty-step", type=float, default=0.001)
    args = parser.parse_args()

    cfg = Config(
        initial_equity=args.initial_equity,
        risk_per_trade=args.risk,
        taker_fee_rate=args.fee,
        slippage_rate=args.slippage,
        min_quantity=args.min_qty,
        quantity_step=args.qty_step,
    )
    data = load_data(Path(args.data))
    outdir = Path(args.out)
    report = split_and_run(data, cfg, outdir)
    print(json.dumps(report, indent=2, default=str))
    print(f"\nResults written to: {outdir.resolve()}")
    print("Inspect report.json, *_trades.csv, and *_equity.csv.")
    print("Do not use live until data quality, contract sizing, fees, funding, and fills are verified.")


if __name__ == "__main__":
    main()
