from core.settings import Settings
from strategies.combo import Strategy as Combo
from strategies.loader import available_strategies, load_strategy


def test_live_mean_reversion_is_discoverable_but_research_scripts_are_not():
    names = available_strategies()
    assert "mean_reversion" in names
    assert "mean_reversion_backtest" not in names
    assert load_strategy("mean_reversion", {}).__class__.__name__ == "Strategy"


def test_configured_combo_loads_every_strategy_with_a_risk_cap():
    settings = Settings.load()
    combo = Combo(subs=settings.strategy_params["subs"], _symbol="BTCUSD")
    tags = [tag for tag, _ in combo.subs]
    assert tags == [
        "ema_5m", "ema_15m", "btc_trend_pullback", "mean_reversion",
        "donchian_breakout", "btc_bms", "time_series_momentum",
    ]
    assert set(tags) == set(settings.risk.strategy_risk_pct)
