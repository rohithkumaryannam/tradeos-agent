from core.settings import Settings
from core.agent import Agent
from strategies.combo import Strategy as Combo
from strategies.loader import available_strategies, load_strategy


def test_live_mean_reversion_is_discoverable_but_research_scripts_are_not():
    names = available_strategies()
    assert "mean_reversion" in names
    assert "mean_reversion_backtest" not in names
    assert load_strategy("mean_reversion", {}).__class__.__name__ == "Strategy"


def test_configured_combo_assigns_one_active_strategy_per_market(tmp_path):
    settings = Settings.load()
    assignments = {
        symbol: [tag for tag, _ in Combo(
            subs=settings.strategy_params["subs"], _symbol=symbol
        ).subs]
        for symbol in settings.symbols
    }
    assert assignments == {
        "BTCUSD": ["donchian_btc"],
        "ETHUSD": ["mean_reversion"],
        "XRPUSD": ["donchian_xrp"],
        "DOGEUSD": ["donchian_breakout"],
        "ADAUSD": ["btc_bms"],
        "1000SHIBUSD": ["time_series_momentum"],
        "SOLUSD": ["darkknight_sol"],
    }
    tags = [items[0] for items in assignments.values()]
    assert set(tags) <= set(settings.risk.strategy_risk_pct)
    settings.trade_log = str(tmp_path / "trades.csv")
    assert set(Agent(settings).snapshot()["active_strategies"]) == set(tags)
