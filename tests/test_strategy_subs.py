import pytest

from core.agent import Agent
from core.settings import RiskConfig, Settings


def agent(tmp_path, subs=None):
    subs = subs or [
        {"name": "donchian_breakout", "tag": "donchian_xrp", "symbols": ["XRPUSD"]},
        {"name": "mean_reversion", "tag": "mean_reversion", "symbols": ["ETHUSD"]},
    ]
    risk = RiskConfig(strategy_risk_pct={"donchian_xrp": 0.25, "mean_reversion": 0.25})
    return Agent(Settings(
        symbols=["XRPUSD", "ETHUSD"], ai_narrate=False, risk=risk,
        strategy_name="combo", strategy_params={"subs": subs},
        trade_log=str(tmp_path / "trades.csv"),
    ))


def test_update_sub_changes_params_without_touching_other_subs(tmp_path):
    a = agent(tmp_path)
    sub = a.update_sub("donchian_xrp", params={"fallback_tp_mult": 2.0})
    assert sub["params"] == {"fallback_tp_mult": 2.0}
    subs = a.s.strategy_params["subs"]
    other = next(s for s in subs if s["tag"] == "mean_reversion")
    assert other.get("params", {}) == {}


def test_update_sub_rejects_unknown_param(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="unknown params"):
        a.update_sub("donchian_xrp", params={"not_a_real_param": 1})


def test_update_sub_rejects_unknown_tag(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="unknown strategy slot"):
        a.update_sub("not_a_tag", params={"fallback_tp_mult": 2.0})


def _active_sub_module(agent_, symbol, tag):
    """The combo instance for `symbol` filters its subs down to the ones
    that apply there — find the one matching `tag` and return its module."""
    for sub_tag, strat in agent_.markets[symbol].strategy.subs:
        if sub_tag == tag:
            return strat.__class__.__module__
    raise AssertionError(f"no sub tagged {tag} active for {symbol}")


def test_update_sub_changes_strategy_and_resets_params(tmp_path):
    a = agent(tmp_path)
    sub = a.update_sub("donchian_xrp", name="mean_reversion")
    assert sub["name"] == "mean_reversion"
    assert sub["params"] == {}
    # the live strategy instance for XRPUSD actually changed
    assert _active_sub_module(a, "XRPUSD", "donchian_xrp").endswith("mean_reversion")


def test_update_sub_rejects_combo_as_a_nested_strategy(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="unknown strategy"):
        a.update_sub("donchian_xrp", name="combo")


def test_update_sub_blocks_strategy_change_with_open_position(tmp_path):
    a = agent(tmp_path)
    a.markets["XRPUSD"].broker.position = object()  # anything truthy
    with pytest.raises(ValueError, match="close the open position"):
        a.update_sub("donchian_xrp", name="mean_reversion")
    # params-only changes are still fine with a position open
    sub = a.update_sub("donchian_xrp", params={"fallback_tp_mult": 3.0})
    assert sub["params"] == {"fallback_tp_mult": 3.0}


def test_update_sub_updates_risk_pct(tmp_path):
    a = agent(tmp_path)
    sub = a.update_sub("donchian_xrp", risk_pct=1.5)
    assert sub is not None
    assert a.s.risk.strategy_risk_pct["donchian_xrp"] == 1.5
    assert a.s.risk.strategy_risk_pct["mean_reversion"] == 0.25


def test_update_sub_rejects_out_of_range_risk_pct(tmp_path):
    a = agent(tmp_path)
    with pytest.raises(ValueError, match="risk_pct"):
        a.update_sub("donchian_xrp", risk_pct=50)


def test_apply_strategy_subs_restores_on_boot(tmp_path):
    a = agent(tmp_path)
    new_subs = [
        {"name": "mean_reversion", "tag": "donchian_xrp", "symbols": ["XRPUSD"], "params": {}},
        {"name": "mean_reversion", "tag": "mean_reversion", "symbols": ["ETHUSD"]},
    ]
    a.apply_strategy_subs(new_subs)
    assert a.s.strategy_params["subs"] == new_subs
    assert _active_sub_module(a, "XRPUSD", "donchian_xrp").endswith("mean_reversion")
