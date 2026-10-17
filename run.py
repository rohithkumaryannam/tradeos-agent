#!/usr/bin/env python3
"""
TradeOS BTC Futures Agent — entrypoint.

Usage:
  python run.py                 # run live loop using config.yaml
  python run.py --once          # run a single evaluation cycle and exit
  python run.py --backtest FILE # replay candles from a JSON file (list of OHLC)
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
import math
from pathlib import Path

from core.settings import Settings
from core.agent import Agent


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


class ReplayClient:
    """Offline market data only; deliberately has no order or account methods."""
    offline_replay = True

    def __init__(self, contract_size: float):
        self.contract_size = contract_size
        self.window = []

    def get_product(self, symbol):
        return {"contract_value": self.contract_size}

    def get_candles(self, *args):
        return self.window


def backtest(agent: Agent, candles: list[dict], warmup: int = 30, fee_bps: float = 5, slippage_bps: float = 2):
    from core.backtest import simulate
    report = simulate(agent, candles, warmup, fee_bps, slippage_bps)
    print(json.dumps(report, indent=2))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--backtest", metavar="FILE", help="replay candles from JSON")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--symbol", help="single instrument represented by the replay file")
    ap.add_argument("--contract-size", type=float, default=0.001, help="base units per replay contract (default BTC: 0.001)")
    ap.add_argument("--fee-bps", type=float, default=5, help="replay fee per side in basis points; illustrative default 5")
    ap.add_argument("--slippage-bps", type=float, default=2, help="adverse replay slippage per fill")
    ap.add_argument("--strategy", help="strategy to replay; does not change running bot")
    ap.add_argument("--resolution", help="timeframe of replay candles, e.g. 1h")
    ap.add_argument("--report", help="write replay metrics as JSON")
    args = ap.parse_args()

    settings = Settings.load(args.config, mode_override="PAPER" if args.backtest else None)
    setup_logging(settings.log_level)

    if args.backtest:
        if not math.isfinite(args.contract_size) or args.contract_size <= 0:
            ap.error("--contract-size must be finite and positive")
        settings.mode = "PAPER"
        settings.api_key = settings.api_secret = ""
        settings.ai_narrate = False
        settings.symbols = [args.symbol or settings.symbols[0]]
        settings.trade_log = "logs/backtest.csv"
        with open(args.backtest) as f:
            candles = json.load(f)
        if args.strategy:
            settings.strategy_name, settings.strategy_params = args.strategy, {}
        if args.resolution:
            settings.resolution = args.resolution
        else:
            from core.agent import _RES_SECONDS
            delta = candles[1]["time"] - candles[0]["time"] if len(candles) > 1 else 0
            inferred = next((name for name, seconds in _RES_SECONDS.items() if seconds == delta), None)
            if not inferred:
                ap.error("cannot infer candle timeframe; supply --resolution")
            settings.resolution = inferred
        agent = Agent(settings, client=ReplayClient(args.contract_size))
        report = backtest(agent, candles, fee_bps=args.fee_bps, slippage_bps=args.slippage_bps)
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        return
    agent = Agent(settings)
    if args.once:
        outcomes = agent.evaluate()
        for sym, out in outcomes.items():
            print(f"{sym}: {out.event}: {out.detail}")
    else:
        try:
            agent.run()
        except KeyboardInterrupt:
            print("\nstopped.")
            sys.exit(0)


if __name__ == "__main__":
    main()
