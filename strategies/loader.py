"""Dynamically loads a strategy module from strategies/ by name."""
from __future__ import annotations
import importlib
import pkgutil

import strategies
from strategies.base import BaseStrategy


def load_strategy(name: str, params: dict) -> BaseStrategy:
    mod = importlib.import_module(f"strategies.{name}")
    if not hasattr(mod, "Strategy"):
        raise RuntimeError(f"strategies/{name}.py must define a `Strategy` class")
    return mod.Strategy(**params)


def available_strategies() -> list[str]:
    """Every selectable strategy module in strategies/ (excludes the framework)."""
    skip = {"base", "loader"}
    return sorted(m.name for m in pkgutil.iter_modules(strategies.__path__)
                  if m.name not in skip and not m.name.startswith("_")
                  and not m.name.endswith("_backtest"))
