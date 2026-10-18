"""Functional tests for the EMA 9/20/200 + ADX crossover strategy. Uses small
periods (fast/mid/trend/adx/atr) so a clean trend can warm everything up in a
reasonably small number of synthetic candles."""
from strategies.ema_crossover import Strategy
from strategies.base import Action


def _candles(prices, spread=1.0, start=1_700_000_000, step=900):
    return [{"time": start + i * step, "open": p, "high": p + spread,
             "low": p - spread, "close": p, "volume": 10}
            for i, p in enumerate(prices)]


def _trend(start, move, n, flat=25):
    vals = [start] * flat
    p = start
    for _ in range(n):
        p += move
        vals.append(p)
    return vals


def _choppy(start, n, amp=0.5, flat=25):
    vals = [start] * flat
    p = start
    for i in range(n):
        p += amp if i % 2 == 0 else -amp
        vals.append(p)
    return vals


def _strat(**kw):
    kw.setdefault("fast", 3)
    kw.setdefault("mid", 5)
    kw.setdefault("trend", 10)
    kw.setdefault("adx_period", 3)
    kw.setdefault("atr_period", 3)
    kw.setdefault("confirm_candles", 1)
    kw.setdefault("swing_lookback", 3)
    kw.setdefault("adx_threshold", 15)
    return Strategy(**kw)


def _run(candles, strat):
    pos, actions, signals = None, [], []
    for i in range(strat.required_candles, len(candles) + 1):
        sig = strat.generate(candles[:i], pos)
        if sig.action != Action.HOLD:
            actions.append(sig.action)
            signals.append(sig)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
        elif sig.action == Action.ENTER_SHORT:
            pos = "short"
        elif sig.action == Action.EXIT:
            pos = None
    return actions, signals


def test_long_entry_on_confirmed_uptrend_with_bracket():
    candles = _candles(_trend(1000.0, +8, 40))
    actions, signals = _run(candles, _strat())
    assert Action.ENTER_LONG in actions
    sig = signals[actions.index(Action.ENTER_LONG)]
    assert sig.stop_loss < sig.take_profit
    assert sig.stop_loss is not None and sig.take_profit is not None


def test_short_entry_on_confirmed_downtrend_with_bracket():
    candles = _candles(_trend(1000.0, -8, 40))
    actions, signals = _run(candles, _strat())
    assert Action.ENTER_SHORT in actions
    sig = signals[actions.index(Action.ENTER_SHORT)]
    assert sig.stop_loss > sig.take_profit


def test_exit_fires_immediately_on_opposite_cross():
    vals = [1000.0] * 25
    p = 1000.0
    for _ in range(40):
        p -= 8
        vals.append(p)
    for _ in range(40):
        p += 9
        vals.append(p)
    candles = _candles(vals)
    actions, _ = _run(candles, _strat())
    assert Action.ENTER_SHORT in actions
    assert Action.EXIT in actions


def test_adx_filter_blocks_entries_in_a_chop():
    candles = _candles(_choppy(1000.0, 60))
    actions, _ = _run(candles, _strat(adx_threshold=40))
    assert Action.ENTER_LONG not in actions
    assert Action.ENTER_SHORT not in actions


def test_holds_while_warming_up():
    candles = _candles(_trend(1000.0, +8, 5))
    strat = _strat()
    assert strat.generate(candles, None).action == Action.HOLD
