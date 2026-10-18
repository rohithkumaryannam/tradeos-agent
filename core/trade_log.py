"""Append-only CSV trade log."""
from __future__ import annotations
import csv
import os
from datetime import datetime, timezone

from core import db

FIELDS = ["ts", "event", "side", "price", "size", "sl", "tp",
          "pnl", "equity", "reason", "strategy", "symbol"]


class TradeLog:
    def __init__(self, path: str):
        self.path = path
        # Backtests/replays can write thousands of rows in seconds — never
        # let them mirror into the shared live Mongo collection. Set False
        # by whatever constructs a TradeLog for an offline run (see
        # core/backtest.py), never flip it off for the live server's own log.
        self.mirror = True
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(FIELDS)
        else:
            self._repair_header()

    def _repair_header(self):
        """Migrate old/headerless logs without discarding their trade rows."""
        with open(self.path, newline="") as f:
            rows = list(csv.reader(f))
        if rows and rows[0] == FIELDS:
            return
        if rows and rows[0] and rows[0][0] == "ts":
            old_fields, data = rows[0], rows[1:]
            migrated = [
                [dict(zip(old_fields, row)).get(field, "") for field in FIELDS]
                for row in data
            ]
        else:
            migrated = [(row + [""] * len(FIELDS))[:len(FIELDS)] for row in rows]
        tmp = self.path + ".repair"
        with open(tmp, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(FIELDS)
            writer.writerows(migrated)
        os.replace(tmp, self.path)

    def write(self, event, side="", price="", size="", sl="", tp="",
              pnl="", equity="", reason="", strategy="", symbol=""):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = [ts, event, side, price, size, sl, tp, pnl, equity, reason, strategy, symbol]
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow(row)
        if self.mirror:
            db.insert_trade(dict(zip(FIELDS, row)))
