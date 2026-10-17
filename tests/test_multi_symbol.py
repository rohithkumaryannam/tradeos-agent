"""Multi-symbol trading: each symbol gets its own broker/position/strategy
instance, but equity and the daily loss cap are shared across all of them,
matching the real account (one Delta balance, not one per market)."""
from core.settings import Settings
from core.agent import Agent
from strategies.base import Action, Signal


def _agent(tmp_path, symbols=("BTCUSD", "ETHUSD")):
    s = Settings()
    s.mode = "PAPER"
    s.symbols = list(symbols)
    s.trade_log = str(tmp_path / "trades.csv")
    return Agent(s)


def test_each_symbol_gets_independent_broker_and_strategy(tmp_path):
    a = _agent(tmp_path)
    btc, eth = a.markets["BTCUSD"], a.markets["ETHUSD"]
    assert btc.broker is not eth.broker
    assert btc.strategy is not eth.strategy


def test_position_in_one_symbol_does_not_block_another(tmp_path):
    a = _agent(tmp_path)
    btc, eth = a.markets["BTCUSD"], a.markets["ETHUSD"]
    btc.last_candles = [{"close": 50_000}]
    a._open_from_signal(btc, Signal(Action.ENTER_LONG, "test"), 50_000)
    assert btc.broker.position is not None
    assert eth.broker.position is None          # untouched

    eth.last_candles = [{"close": 3_000}]
    a._open_from_signal(eth, Signal(Action.ENTER_SHORT, "test"), 3_000)
    assert eth.broker.position is not None
    assert btc.broker.position is not None       # still open, unaffected


def test_equity_and_daily_cap_are_shared_across_symbols(tmp_path):
    a = _agent(tmp_path)
    start_equity = a.risk.equity
    btc = a.markets["BTCUSD"]
    btc.last_candles = [{"close": 50_000}]
    a._open_from_signal(btc, Signal(Action.ENTER_LONG, "test"), 50_000)
    a._do_exit(btc, 49_000, "stop-loss hit")     # a real loss
    assert a.risk.equity < start_equity
    # the SAME risk engine (shared equity) governs ETH's sizing too
    eth = a.markets["ETHUSD"]
    plan = a.risk.size_position("long", 3_000, contract_value=eth.broker.contract_value(3_000))
    assert plan.ok or "daily loss cap" in plan.reject_reason  # sized off shared equity either way


def test_trades_are_tagged_with_their_own_symbol(tmp_path):
    a = _agent(tmp_path)
    btc, eth = a.markets["BTCUSD"], a.markets["ETHUSD"]
    btc.last_candles = [{"close": 50_000}]
    a._open_from_signal(btc, Signal(Action.ENTER_LONG, "test"), 50_000)
    a._do_exit(btc, 50_500, "take-profit hit")
    eth.last_candles = [{"close": 3_000}]
    a._open_from_signal(eth, Signal(Action.ENTER_SHORT, "test"), 3_000)
    a._do_exit(eth, 2_950, "take-profit hit")

    symbols_traded = {t["symbol"] for t in a.trades}
    assert symbols_traded == {"BTCUSD", "ETHUSD"}


def test_force_close_targets_the_right_symbol(tmp_path):
    a = _agent(tmp_path)
    btc, eth = a.markets["BTCUSD"], a.markets["ETHUSD"]
    btc.last_candles, eth.last_candles = [{"close": 50_000}], [{"close": 3_000}]
    a._open_from_signal(btc, Signal(Action.ENTER_LONG, "test"), 50_000)
    a._open_from_signal(eth, Signal(Action.ENTER_SHORT, "test"), 3_000)

    out = a.force_close("BTCUSD")
    assert out.event == "exit"
    assert btc.broker.position is None
    assert eth.broker.position is not None       # ETH untouched


def test_snapshot_reports_all_markets(tmp_path):
    a = _agent(tmp_path, symbols=("BTCUSD", "ETHUSD", "SOLUSD"))
    snap = a.snapshot()
    assert set(snap["markets"].keys()) == {"BTCUSD", "ETHUSD", "SOLUSD"}
    assert snap["symbols"] == ["BTCUSD", "ETHUSD", "SOLUSD"]
