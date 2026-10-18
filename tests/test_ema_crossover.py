"""Functional tests for the pure EMA 9/20 crossover, stop-and-reverse —
EMA9 crossing EMA20 should fire the matching entry/exit regardless of
direction, with no trend/chop filter."""
from strategies.ema_crossover import Strategy
from strategies.base import Action


def _trend_candles(start, step, n, flat_warmup=10):
    vals = [start] * flat_warmup
    p = start
    for _ in range(n):
        p += step
        vals.append(p)
    return [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 1,
             "low": v - 1, "close": v, "volume": 10} for i, v in enumerate(vals)]


def _run(candles, strat):
    pos, actions = None, []
    for i in range(10, len(candles)):
        sig = strat.generate(candles[: i + 1], pos)
        if sig.action != Action.HOLD:
            actions.append(sig.action)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
        elif sig.action == Action.ENTER_SHORT:
            pos = "short"
        elif sig.action == Action.EXIT:
            pos = None
    return actions


def test_long_on_cross_up():
    candles = _trend_candles(1000.0, +6, 40)   # clean uptrend -> EMA9 crosses above EMA20
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_LONG in actions


def test_short_on_cross_down():
    candles = _trend_candles(1000.0, -5, 40)   # clean downtrend -> EMA9 crosses below EMA20
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_SHORT in actions


def test_exit_fires_on_opposite_cross():
    # downtrend into an uptrend: short entry, then exit on the up-cross
    vals = [1000.0] * 10
    p = 1000.0
    for _ in range(40):
        p -= 5
        vals.append(p)
    for _ in range(40):
        p += 6
        vals.append(p)
    candles = [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 1,
               "low": v - 1, "close": v, "volume": 10} for i, v in enumerate(vals)]
    actions = _run(candles, Strategy(fast=3, mid=8))
    assert Action.ENTER_SHORT in actions
    assert Action.EXIT in actions


def test_reverses_into_the_opposite_side_after_exit():
    # same down-then-up sequence: on the candle where the up-cross exits the
    # short, re-evaluating that SAME closed candle once flat (exactly what
    # the live agent does on its next poll tick, before a new candle closes)
    # must flip it straight into a long.
    vals = [1000.0] * 10
    p = 1000.0
    for _ in range(40):
        p -= 5
        vals.append(p)
    for _ in range(40):
        p += 6
        vals.append(p)
    candles = [{"time": 1_700_000_000 + i * 300, "open": v, "high": v + 1,
               "low": v - 1, "close": v, "volume": 10} for i, v in enumerate(vals)]
    strat = Strategy(fast=3, mid=8)
    pos, actions = None, []
    for i in range(10, len(candles)):
        window = candles[: i + 1]
        sig = strat.generate(window, pos)
        actions.append(sig.action)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
        elif sig.action == Action.ENTER_SHORT:
            pos = "short"
        elif sig.action == Action.EXIT:
            pos = None
            # same poll tick, same still-current closed candle, now flat
            sig2 = strat.generate(window, pos)
            actions.append(sig2.action)
            if sig2.action == Action.ENTER_LONG:
                pos = "long"
            elif sig2.action == Action.ENTER_SHORT:
                pos = "short"
    assert Action.EXIT in actions
    exit_i = actions.index(Action.EXIT)
    assert actions[exit_i + 1] == Action.ENTER_LONG


def test_no_stop_loss_take_profit_set_by_strategy():
    """Bracket comes entirely from the risk config's stop_loss_pct/
    take_profit_pct (1%/2%), not from the strategy."""
    candles = _trend_candles(1000.0, +6, 40)
    actions, signals = [], []
    strat = Strategy(fast=3, mid=8)
    pos = None
    for i in range(10, len(candles)):
        sig = strat.generate(candles[: i + 1], pos)
        if sig.action != Action.HOLD:
            actions.append(sig.action)
            signals.append(sig)
        if sig.action == Action.ENTER_LONG:
            pos = "long"
    assert Action.ENTER_LONG in actions
    entry_sig = signals[actions.index(Action.ENTER_LONG)]
    assert entry_sig.stop_loss is None
    assert entry_sig.take_profit is None


def test_ema_crossover_holds_while_warming_up():
    candles = _trend_candles(1000.0, +6, 2)
    assert Strategy(fast=9, mid=20).generate(candles, None).action == Action.HOLD
