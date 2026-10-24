#!/usr/bin/env python3
"""Compare fixed strategy candidates using public data and isolated paper agents.

python research.py --download --days 180
No credentials are read or sent by the downloader; it only calls public GETs.
"""
import argparse
import hashlib
import json
import logging
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from core.agent import Agent, _RES_SECONDS
from core.backtest import simulate, validate_candles
from core.delta_client import DeltaClient
from core.settings import Settings, RiskConfig
from run import ReplayClient

ROOT = Path(__file__).resolve().parent
CANDIDATES = ("ema_crossover", "donchian_breakout", "btc_trend_pullback",
              "mean_reversion", "btc_bms", "time_series_momentum", "darkknight_sol",
              "supertrend", "bb_squeeze_breakout", "mean_reversion_scalp")
PUBLIC_API = "https://api.india.delta.exchange"


def download(symbol, resolution, days, directory):
    seconds = _RES_SECONDS[resolution]
    end = int(time.time()) // seconds * seconds
    start = end - days * 86400
    start = start // seconds * seconds
    client = DeltaClient(base_url=PUBLIC_API)  # intentionally empty credentials
    contract_size = float(client.get_product(symbol)["contract_value"])
    if not math.isfinite(contract_size) or contract_size <= 0:
        raise ValueError("invalid contract specification")
    rows = {}
    for cursor in range(start, end, seconds * 1000):
        chunk_end = min(end, cursor + seconds * 1000)
        chunk = client.get_candles(symbol, resolution, cursor, chunk_end)
        for candle in chunk:
            stamp = int(candle["time"])
            if cursor <= stamp < chunk_end:
                rows[stamp] = candle
    candles = [rows[t] for t in sorted(rows)]
    validate_candles(candles, seconds)
    if len(candles) != (end - start) // seconds:
        raise ValueError(f"{symbol}: incomplete history ({len(candles)} candles); comparison aborted")
    payload = {"symbol": symbol, "resolution": resolution, "contract_size": contract_size,
               "source": PUBLIC_API, "downloaded_at": datetime.now(timezone.utc).isoformat(),
               "candles": candles}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{symbol}-{resolution}.json"
    path.write_text(json.dumps(payload))
    return path


def compare_dataset(payload, fee_bps=5, slippage_bps=2, candidates=CANDIDATES):
    candles = payload["candles"]
    if len(candles) < 1000:
        raise ValueError("comparison requires at least 1,000 candles")
    # Defaults and the 70/30 boundary are fixed before evaluating any returns.
    # No optimization and no 'best parameters' search.
    split = int(len(candles) * .7)
    rows = []
    with tempfile.TemporaryDirectory(prefix="tradeos-research-") as directory:
        for strategy in candidates:
            for sample, warmup in (("full", 250), ("holdout", split - 1)):
                # Strategies with a configurable `resolution` constructor
                # param (ema_crossover, supertrend, bb_squeeze_breakout)
                # default to their own fixed timeframe otherwise, silently
                # mismatching the dataset actually being replayed.
                strategy_params = ({"resolution": payload["resolution"]}
                                    if strategy in ("ema_crossover", "supertrend", "bb_squeeze_breakout", "mean_reversion_scalp")
                                    else {})
                settings = Settings(
                    mode="PAPER", symbols=[payload["symbol"]], resolution=payload["resolution"],
                    lookback=300, strategy_name=strategy, strategy_params=strategy_params,
                    ai_narrate=False,
                    risk=RiskConfig(account_equity=10_000, risk_per_trade_pct=.5, max_leverage=2),
                    trade_log=str(Path(directory) / f"{strategy}-{sample}.csv"),
                )
                a = Agent(settings, client=ReplayClient(float(payload["contract_size"])))
                result = simulate(a, candles, warmup=warmup, fee_bps=fee_bps, slippage_bps=slippage_bps)
                result["sample"] = sample
                # Exclude injected plumbing (e.g. _fetch_candles, a bound
                # method) that every strategy receives but isn't JSON-safe.
                result["strategy_params"] = {k: v for k, v in
                    a.markets[payload["symbol"]].strategy.params.items()
                    if not k.startswith("_")}
                result["sample_note"] = "Too few trades for a stable estimate" if result["analytics"]["trades"] < 30 else "Historical estimate; forward validation required"
                rows.append(result)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD", "ETHUSD", "SOLUSD"])
    parser.add_argument("--resolution", choices=list(_RES_SECONDS), default="1h")
    parser.add_argument("--candidates", nargs="+", choices=CANDIDATES, default=list(CANDIDATES))
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--fee-bps", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=2)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "research")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "strategy_comparison.json")
    args = parser.parse_args()
    if not 45 <= args.days <= 730:
        parser.error("days must be between 45 and 730")
    logging.basicConfig(level=logging.WARNING)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "research_only", "selection": "Fixed candidates selected from literature; not a ranking of all strategies",
        "holdout_fraction": .3, "risk_per_trade_pct": .5, "max_leverage": 2,
        "candidates": args.candidates, "resolution": args.resolution,
        "warning": "No profit guarantee. Funding, liquidation and market impact are excluded. The holdout is a chronological diagnostic, not evidence of a statistically validated edge. Do not select or tune parameters using this holdout and still call it untouched.",
        "datasets": [], "results": [],
    }
    for symbol in args.symbols:
        path = download(symbol, args.resolution, args.days, args.data_dir) if args.download else args.data_dir / f"{symbol}-{args.resolution}.json"
        raw = path.read_bytes()
        payload = json.loads(raw)
        report["datasets"].append({"symbol": payload["symbol"], "source": payload["source"],
                                   "sha256": hashlib.sha256(raw).hexdigest(), "candles": len(payload["candles"])})
        results = compare_dataset(payload, args.fee_bps, args.slippage_bps, args.candidates)
        report["results"].extend(results)
        for row in results:
            if row["sample"] == "holdout":
                print(f'{symbol:7} {row["strategy"]:22} net={row["return_pct"]:+.2f}% DD={row["max_drawdown_pct"]:.2f}% trades={row["analytics"]["trades"]}', flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2) + "\n")
    tmp.replace(args.output)
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
