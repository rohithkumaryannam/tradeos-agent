from core.settings import Settings
from core.agent import Agent
from strategies.combo import Strategy as Combo
from strategies.loader import available_strategies, load_strategy


def test_live_mean_reversion_is_discoverable_but_research_scripts_are_not():
    names = available_strategies()
    assert "mean_reversion" in names
    assert "mean_reversion_backtest" not in names
    assert load_strategy("mean_reversion", {}).__class__.__name__ == "Strategy"


def test_configured_combo_loads_every_strategy_with_a_risk_cap(tmp_path):
    settings = Settings.load()
    assignments = {
        symbol: [tag for tag, _ in Combo(
            subs=settings.strategy_params["subs"], _symbol=symbol
        ).subs]
        for symbol in settings.symbols
    }
    assert assignments == {
        "BTCUSD": ["time_series_momentum"],
        "ETHUSD": ["btc_trend_pullback"],
        "SOLUSD": ["donchian_breakout"],
        "XRPUSD": ["mean_reversion"],
        "DOGEUSD": ["ema_5m"],
        "ADAUSD": ["btc_bms"],
        "1000SHIBUSD": ["ema_15m"],
    }
    tags = [items[0] for items in assignments.values()]
    assert set(tags) == set(settings.risk.strategy_risk_pct)
    settings.trade_log = str(tmp_path / "trades.csv")
    assert set(Agent(settings).snapshot()["active_strategies"]) == set(tags)
