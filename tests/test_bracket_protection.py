"""Protection-status reporting: must distinguish a real exchange-side bracket
(verified via Delta's own API) from local-only bot-loop enforcement, and never
claim "exchange" protection that wasn't actually confirmed. See core/broker.py
DeltaBroker.open() / has_bracket() for the live-verified mechanism."""
from core.settings import Settings
from core.agent import Agent
from core.broker import Position


def _agent(tmp_path):
    s = Settings()
    s.mode = "PAPER"
    s.trade_log = str(tmp_path / "trades.csv")
    return Agent(s)


def test_paper_position_is_never_exchange_protected(tmp_path):
    a = _agent(tmp_path)
    pos = Position(side="long", entry=50_000, size=1, stop_loss=49_000,
                   take_profit=52_000, notional=50, bracket_confirmed=False)
    assert a._protection_status(pos) == "local"


def test_confirmed_bracket_reports_exchange(tmp_path):
    a = _agent(tmp_path)
    pos = Position(side="long", entry=50_000, size=1, stop_loss=49_000,
                   take_profit=52_000, notional=50, bracket_confirmed=True)
    assert a._protection_status(pos) == "exchange"


def test_unconfirmed_delta_bracket_falls_back_to_local(tmp_path):
    a = _agent(tmp_path)
    # DELTA_DEMO entry where Delta didn't confirm the bracket (bracket_confirmed
    # stays False) — must NOT claim exchange protection it doesn't have.
    pos = Position(side="short", entry=3_000, size=5, stop_loss=3_100,
                   take_profit=2_800, notional=100, bracket_confirmed=False)
    assert a._protection_status(pos) == "local"


def test_no_sl_tp_reports_none(tmp_path):
    a = _agent(tmp_path)
    pos = Position(side="long", entry=50_000, size=1, stop_loss=None,
                   take_profit=None, notional=50, bracket_confirmed=True)
    assert a._protection_status(pos) == "none"
