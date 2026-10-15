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

from core.settings import Settings
from core.agent import Agent


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def backtest(agent: Agent, candles: list[dict], warmup: int = 30):
    """Replay historical candles through the live decision logic."""
    print(f"backtest: {len(candles)} candles")
    for i in range(warmup, len(candles)):
        window = candles[: i + 1]
        agent.client.get_candles = lambda *a, _w=window, **k: _w  # feed the slice
        agent.evaluate()
    # close any open position at the last price
    if agent.broker.position:
        price = float(candles[-1]["close"])
        side = agent.broker.position.side
        pnl = agent.broker.close(price)
        agent.risk.register_pnl(pnl)
        agent._log_exit(side, price, pnl, "backtest end")
    print(f"final equity: {agent.risk.equity:.2f} "
          f"(start {agent.s.risk.account_equity:.2f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--backtest", metavar="FILE", help="replay candles from JSON")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    args = ap.parse_args()

    settings = Settings.load(args.config) if args.config else Settings.load()
    setup_logging(settings.log_level)

    agent = Agent(settings)

    if args.backtest:
        with open(args.backtest) as f:
            candles = json.load(f)
        backtest(agent, candles)
    elif args.once:
        out = agent.evaluate()
        print(f"{out.event}: {out.detail}")
    else:
        try:
            agent.run()
        except KeyboardInterrupt:
            print("\nstopped.")
            sys.exit(0)


if __name__ == "__main__":
    main()
