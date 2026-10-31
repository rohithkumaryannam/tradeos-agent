from strategies.paused import Strategy
from strategies.base import Action


def test_paused_never_enters_when_flat():
    assert Strategy().generate([], None).action == Action.HOLD


def test_paused_holds_an_existing_open_position():
    assert Strategy().generate([], "short").action == Action.HOLD
