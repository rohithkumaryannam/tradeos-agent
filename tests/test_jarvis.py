import pytest

from core import ai
from core.agent import Agent
from core.settings import RiskConfig, Settings


def agent(tmp_path):
    risk = RiskConfig(strategy_risk_pct={"ema_crossover": 0.5})
    return Agent(Settings(symbols=["BTCUSD", "ETHUSD"], ai_narrate=False,
                          risk=risk, trade_log=str(tmp_path / "trades.csv")))


def test_command_reports_unavailable_when_ai_not_configured(tmp_path):
    a = agent(tmp_path)
    result = a.jarvis_command("what's my status")
    assert result["action"] is None
    assert "configured" in result["reply"].lower()


def test_command_answers_directly_with_no_action(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "ask_json", lambda *a, **k: {
        "reply": "You're flat on both symbols, equity is 10000.", "action": None})
    a = agent(tmp_path)
    result = a.jarvis_command("how am I doing")
    assert result["action"] is None
    assert "flat" in result["reply"]


def test_command_proposes_a_whitelisted_action(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "ask_json", lambda *a, **k: {
        "reply": "Closing your BTCUSD position now.",
        "action": {"name": "close_position", "args": {"symbol": "BTCUSD"}}})
    a = agent(tmp_path)
    result = a.jarvis_command("close my bitcoin position")
    assert result["action"] == {"name": "close_position", "args": {"symbol": "BTCUSD"}}


def test_command_drops_an_action_name_outside_the_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "ask_json", lambda *a, **k: {
        "reply": "Sure, doing that.",
        "action": {"name": "delete_everything", "args": {}}})
    a = agent(tmp_path)
    result = a.jarvis_command("do something destructive")
    assert result["action"] is None


def test_command_handles_malformed_model_output(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "ask_json", lambda *a, **k: None)
    a = agent(tmp_path)
    result = a.jarvis_command("anything")
    assert result["action"] is None
    assert result["reply"]


def test_execute_pause_entries(tmp_path):
    a = agent(tmp_path)
    assert a.entries_paused is False
    out = a.jarvis_execute("pause_entries", {"paused": True})
    assert out == {"entries_paused": True}
    assert a.entries_paused is True


def test_execute_set_auto(tmp_path):
    a = agent(tmp_path)
    out = a.jarvis_execute("set_auto", {"enabled": True})
    assert out == {"auto": True}
    assert a.auto is True


def test_execute_close_position_rejects_unknown_symbol(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="unknown symbol"):
        a.jarvis_execute("close_position", {"symbol": "DOGEUSD"})


def test_execute_set_strategy_risk_pct_merges_without_touching_others(tmp_path):
    a = agent(tmp_path)
    before = dict(a.s.risk.strategy_risk_pct)
    strategy = next(iter(before))
    out = a.jarvis_execute("set_strategy_risk_pct", {"strategy": strategy, "risk_pct": 1.5})
    assert out == {"strategy": strategy, "risk_pct": 1.5}
    assert a.s.risk.strategy_risk_pct[strategy] == 1.5
    for k, v in before.items():
        if k != strategy:
            assert a.s.risk.strategy_risk_pct[k] == v


def test_execute_set_strategy_risk_pct_rejects_out_of_range(tmp_path):
    a = agent(tmp_path)
    strategy = next(iter(a.s.risk.strategy_risk_pct))
    with pytest.raises(ValueError, match="risk_pct"):
        a.jarvis_execute("set_strategy_risk_pct", {"strategy": strategy, "risk_pct": 50})


def test_execute_rejects_unknown_action(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="unknown action"):
        a.jarvis_execute("nuke_account", {})
