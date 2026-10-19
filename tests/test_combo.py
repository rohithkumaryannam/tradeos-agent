"""Combo meta-strategy: it should take the first sub-strategy's entry and tag
the signal with that strategy's name, and only the owning strategy manages
the exit."""
from strategies.combo import Strategy
from strategies.base import Action


def _candle(p, vol=10, spread=0.5):
    return {"high": p + spread, "low": p - spread, "close": p, "open": p, "volume": vol}


def _breakout_candles():
    """Flat candles then a single steady uptrend: ema_crossover's one cross
    happens early and is "used up" well before candle 40 (no re-cross in a
    clean monotonic trend), so by then it holds "no cross" and lets the
    later, volume-confirmed donchian_breakout signal through instead."""
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


def _trend(start, move, n, flat=25):
    vals = [start] * flat
    p = start
    for _ in range(n):
        p += move
        vals.append(p)
    return vals


def _timed_candles(vals, step):
    return [{"time": i * step, "open": v, "high": v + 1, "low": v - 1,
             "close": v, "volume": 10} for i, v in enumerate(vals)]


def test_two_timeframe_instances_tagged_separately_and_fetch_independently():
    """Two ema_crossover subs, same module, different resolutions: each must
    be attributed by its own tag, and the 15m one must fetch ITS OWN candles
    (via _fetch_candles) rather than reusing the 5m base feed."""
    base_candles = _timed_candles(_trend(1000.0, 0, 60), step=300)   # 5m, perfectly flat — no cross ever
    fifteen_m_full = _timed_candles(_trend(1000.0, +6, 60), step=900)   # 15m, clean cross

    def fetch(symbol, resolution):
        fetch.calls.append((symbol, resolution))
        return fifteen_m_full[:fetch.i]
    fetch.calls = []

    actions = []
    for i in range(25, len(base_candles) + 1):
        fetch.i = i
        combo = Strategy(
            subs=[
                {"name": "ema_crossover", "params": {"fast": 3, "mid": 8, "resolution": "5m"}, "tag": "ema_5m"},
                {"name": "ema_crossover", "params": {"fast": 3, "mid": 8, "resolution": "15m"}, "tag": "ema_15m"},
            ],
            _symbol="BTCUSD", _fetch_candles=fetch,
        )
        sig = combo.generate(base_candles[:i], None)
        if sig.action != Action.HOLD:
            actions.append(sig)
    assert [tag for tag, _ in combo.subs] == ["ema_5m", "ema_15m"]
    assert actions, "expected the 15m sub to fire at some point"
    assert actions[0].strategy == "ema_15m"
    assert ("BTCUSD", "15m") in fetch.calls


def test_sub_without_fetch_injection_falls_back_to_shared_candles():
    """Backward compatible: no _fetch_candles/_symbol given (e.g. a plain
    Strategy(subs=[...]) in a test, or a resolution mismatch nobody wired
    up) -> every sub just uses the one shared candle feed, same as before
    multi-timeframe support existed."""
    candles = _timed_candles(_trend(1000.0, +6, 40), step=300)
    combo = Strategy(subs=[
        {"name": "ema_crossover", "params": {"fast": 3, "mid": 8, "resolution": "15m"}, "tag": "ema_15m"},
    ])
    actions = []
    for i in range(10, len(candles) + 1):
        sig = combo.generate(candles[:i], None)
        if sig.action != Action.HOLD:
            actions.append(sig)
            break
    assert actions
    assert actions[0].strategy == "ema_15m"   # still fires, just off the shared feed
