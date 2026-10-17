"""Two behaviors requested after real-money testing surfaced them:
1. Once a real exchange bracket is confirmed, the bot must NOT cut the trade
   early on a strategy exit signal — only Delta's own SL/TP (or a manual
   close) should end it.
2. A restart must not orphan a position already open on Delta — DeltaBroker
   reconciles with the live exchange on construction instead of assuming flat,
   so the bot never risks opening a conflicting second position.
"""
from core.settings import Settings
from core.agent import Agent
from core.broker import Position, DeltaBroker
from strategies.base import Action, Signal


class _FakeClient:
    """Minimal DeltaClient stand-in — no network calls."""
    def __init__(self, positions=None, bracket_prices=(None, None)):
        self._positions = positions or []
        self._bracket_prices = bracket_prices

    def get_product(self, symbol):
        return {"id": 84, "contract_value": 0.001}

    def get_positions(self):
        return self._positions

    def get_bracket_prices(self, product_id):
        return self._bracket_prices


def test_bracket_confirmed_exit_signal_is_ignored(tmp_path, monkeypatch):
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    a = Agent(s)
    m = a.markets["BTCUSD"]

    # open a bracket-confirmed position directly (simulating a live Delta trade)
    m.broker.position = Position(side="long", entry=50_000, size=1, stop_loss=49_000,
                                 take_profit=52_000, notional=50, bracket_confirmed=True)

    class _AlwaysExit:
        def generate(self, candles, position_side):
            return Signal(Action.EXIT, "opposite cross")
    m.strategy = _AlwaysExit()
    fake_candles = [{"time": 1, "open": 50_100, "high": 50_200, "low": 50_050,
                     "close": 50_100, "volume": 1}] * 10
    a.client.get_candles = lambda *args, **kw: fake_candles

    out = a._evaluate_market(m)
    assert out.event == "hold"
    assert m.broker.position is not None          # NOT cut early
    assert "exchange bracket owns the exit" in out.detail


def test_unconfirmed_bracket_exit_signal_still_fires(tmp_path):
    """Without a confirmed exchange bracket (paper, or an unconfirmed Delta
    entry), the strategy exit must still work — it's the only protection."""
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    a = Agent(s)
    m = a.markets["BTCUSD"]
    m.broker.position = Position(side="long", entry=50_000, size=1, stop_loss=49_000,
                                 take_profit=52_000, notional=50, bracket_confirmed=False)

    class _AlwaysExit:
        def generate(self, candles, position_side):
            return Signal(Action.EXIT, "opposite cross")
    m.strategy = _AlwaysExit()
    fake_candles = [{"time": 1, "open": 50_100, "high": 50_200, "low": 50_050,
                     "close": 50_100, "volume": 1}] * 10
    a.client.get_candles = lambda *args, **kw: fake_candles

    out = a._evaluate_market(m)
    assert out.event == "exit"
    assert m.broker.position is None


def test_reconcile_adopts_existing_position_with_bracket():
    client = _FakeClient(
        positions=[{"product_id": 84, "size": "5", "entry_price": "50000"}],
        bracket_prices=(49_000.0, 52_000.0),
    )
    b = DeltaBroker(client, "BTCUSD")
    assert b.position is not None
    assert b.position.side == "long"
    assert b.position.size == 5
    assert b.position.stop_loss == 49_000.0
    assert b.position.take_profit == 52_000.0
    assert b.position.bracket_confirmed is True


def test_reconcile_adopts_short_without_bracket_honestly(caplog):
    client = _FakeClient(
        positions=[{"product_id": 84, "size": "-3", "entry_price": "50000"}],
        bracket_prices=(None, None),   # no bracket found
    )
    b = DeltaBroker(client, "BTCUSD")
    assert b.position is not None
    assert b.position.side == "short"
    assert b.position.stop_loss is None and b.position.take_profit is None
    assert b.position.bracket_confirmed is False   # never falsely claim protection


def test_reconcile_stays_flat_when_no_exchange_position():
    client = _FakeClient(positions=[])
    b = DeltaBroker(client, "BTCUSD")
    assert b.position is None
