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


def test_owner_is_recovered_from_persisted_bracket_after_a_restart(monkeypatch):
    """A fresh combo instance (e.g. the process restarted while a position
    was open) doesn't remember who opened it in memory. It must recover
    ownership from what the agent persisted at entry time (core.db), or an
    already-open position never gets a reversal/exit signal again."""
    from core import db
    monkeypatch.setattr(db, "load_position_bracket", lambda symbol: {
        "side": "long", "entry": 82517.5, "stop_loss": 81692.3,
        "take_profit": 84167.9, "strategy": "ema_15m",
    })
    combo = Strategy(
        subs=[
            {"name": "ema_crossover", "params": {"resolution": "5m"}, "tag": "ema_5m"},
            {"name": "ema_crossover", "params": {"resolution": "15m"}, "tag": "ema_15m"},
        ],
        _symbol="BTCUSD",
    )
    assert combo._owner is None   # fresh instance, no in-memory record

    candles = _timed_candles(_trend(1000.0, -6, 40), step=300)   # down-cross -> exit a long
    sig = None
    for i in range(10, len(candles) + 1):
        sig = combo.generate(candles[:i], "long")
        if sig.action == Action.EXIT:
            break
    assert sig.action == Action.EXIT
    assert sig.strategy == "ema_15m"   # recovered, not left stuck on None forever


def test_symbols_dedicates_a_sub_to_specific_markets():
    """Giving each sub its own `symbols` means they never compete for the
    same market's position -- needed so two instances of the same strategy
    (e.g. ema_5m and ema_15m) can both actually trade and be compared fairly,
    instead of a faster one always winning the race for every symbol."""
    specs = [
        {"name": "ema_crossover", "params": {"resolution": "5m"}, "tag": "ema_5m", "symbols": ["BTCUSD"]},
        {"name": "ema_crossover", "params": {"resolution": "15m"}, "tag": "ema_15m", "symbols": ["ETHUSD"]},
    ]
    btc_combo = Strategy(subs=specs, _symbol="BTCUSD")
    eth_combo = Strategy(subs=specs, _symbol="ETHUSD")
    assert [tag for tag, _ in btc_combo.subs] == ["ema_5m"]
    assert [tag for tag, _ in eth_combo.subs] == ["ema_15m"]


def test_symbols_filtering_does_not_change_shared_resolution_metadata():
    """Every market's combo instance must report the SAME
    preferred_resolution/supported_resolutions/required_candles regardless
    of which subs apply to it, or the agent's per-market resolution gate
    would block whichever market's own filtered sub doesn't match the
    globally self-healed resolution."""
    specs = [
        {"name": "ema_crossover", "params": {"resolution": "5m"}, "tag": "ema_5m", "symbols": ["BTCUSD"]},
        {"name": "ema_crossover", "params": {"resolution": "15m"}, "tag": "ema_15m", "symbols": ["ETHUSD"]},
    ]
    btc_combo = Strategy(subs=specs, _symbol="BTCUSD")
    eth_combo = Strategy(subs=specs, _symbol="ETHUSD")
    assert btc_combo.preferred_resolution == eth_combo.preferred_resolution
    assert btc_combo.supported_resolutions == eth_combo.supported_resolutions
    assert btc_combo.required_candles == eth_combo.required_candles
