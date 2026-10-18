"""Functional tests for the Donchian channel breakout strategy."""
from strategies.donchian_breakout import Strategy
from strategies.base import Action


def _candle(p, vol=10, spread=0.5):
    return {"high": p + spread, "low": p - spread, "close": p, "open": p, "volume": vol}


def _strat(**kw):
    kw.setdefault("entry_period", 5)
    kw.setdefault("exit_period", 3)
    kw.setdefault("volume_mult", 1.1)
    kw.setdefault("atr_period", 3)
    kw.setdefault("adx_period", 3)
    kw.setdefault("adx_threshold", 0)
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


def test_long_entry_on_breakout_with_volume_confirmation():
    candles = [_candle(100, vol=10) for _ in range(30)]
    candles.append(_candle(103, vol=50))
    candles += [_candle(103 + i, vol=12) for i in range(5)]
    actions, signals = _run(candles, _strat())
    assert Action.ENTER_LONG in actions
    sig = signals[actions.index(Action.ENTER_LONG)]
    assert sig.stop_loss < sig.take_profit


def test_short_entry_on_breakdown_with_volume_confirmation():
    candles = [_candle(100, vol=10) for _ in range(30)]
    candles.append(_candle(97, vol=50))
    candles += [_candle(97 - i, vol=12) for i in range(5)]
    actions, signals = _run(candles, _strat())
    assert Action.ENTER_SHORT in actions
    sig = signals[actions.index(Action.ENTER_SHORT)]
    assert sig.stop_loss > sig.take_profit


def test_breakout_without_volume_confirmation_is_filtered():
    candles = [_candle(100, vol=10) for _ in range(30)]
    candles.append(_candle(103, vol=8))  # below volume_mult x average
    candles += [_candle(103 + i, vol=8) for i in range(5)]
    actions, _ = _run(candles, _strat())
    assert Action.ENTER_LONG not in actions


def test_trailing_exit_fires_after_entry():
    candles = [_candle(100, vol=10) for _ in range(30)]
    candles.append(_candle(103, vol=50))
    candles += [_candle(103 + i, vol=12) for i in range(5)]
    candles += [_candle(107 - i * 3, vol=12) for i in range(5)]
    actions, _ = _run(candles, _strat())
    assert Action.ENTER_LONG in actions
    assert Action.EXIT in actions


def test_holds_while_warming_up():
    candles = [_candle(100) for _ in range(5)]
    strat = _strat()
    assert strat.generate(candles, None).action == Action.HOLD
