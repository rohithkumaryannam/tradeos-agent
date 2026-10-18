"""Combo meta-strategy: it should take the first sub-strategy's entry and tag
the signal with that strategy's name, and only the owning strategy manages
the exit."""
from strategies.combo import Strategy
from strategies.base import Action


def _candle(p, vol=10, spread=0.5):
    return {"high": p + spread, "low": p - spread, "close": p, "open": p, "volume": vol}


def _breakout_candles():
    """38 flat candles (keeps ema_crossover's 212-candle warmup unmet, so it
    can never fire here) then a volume-confirmed uptrend that donchian_breakout
    (38-candle warmup) can act on."""
    candles = [_candle(100, vol=15) for _ in range(15)]
    p = 100
    for i in range(50):
        p += 0.8
        vol = 45 if i % 4 == 0 else 15
        candles.append(_candle(p, vol=vol))
    return candles


def test_combo_tags_the_owning_strategy():
    candles = _breakout_candles()[:40]
    sig = Strategy(subs=["ema_crossover", "donchian_breakout"]).generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.strategy == "donchian_breakout"
    assert sig.reason.startswith("[donchian_breakout]")
    assert sig.stop_loss < sig.take_profit


def test_only_the_owning_strategy_manages_the_exit():
    candles = _breakout_candles()
    combo = Strategy(subs=["ema_crossover", "donchian_breakout"])
    entry = combo.generate(candles[:40], None)
    assert entry.action == Action.ENTER_LONG
    assert combo._owner == "donchian_breakout"
    # while in position, combo must defer to the owner (donchian_breakout),
    # never let ema_crossover's logic drive the exit
    held = combo.generate(candles[:45], "long")
    assert held.action in (Action.HOLD, Action.EXIT)
    assert "donchian_breakout" in held.reason or held.strategy == "donchian_breakout"


def test_default_combo_includes_every_live_strategy():
    assert [name for name, _ in Strategy().subs] == ["ema_crossover", "donchian_breakout"]
