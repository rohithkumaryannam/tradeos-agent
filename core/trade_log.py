"""Append-only CSV trade log."""
from __future__ import annotations
import csv
import os
from datetime import datetime, timezone

FIELDS = ["ts", "event", "side", "price", "size", "sl", "tp",
          "pnl", "equity", "reason"]


class TradeLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(FIELDS)

    def write(self, event, side="", price="", size="", sl="", tp="",
              pnl="", equity="", reason=""):
        row = [
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            event, side, price, size, sl, tp, pnl, equity, reason,
        ]
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow(row)
