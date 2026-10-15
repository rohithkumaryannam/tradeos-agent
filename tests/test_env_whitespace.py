"""Regression test: hosting dashboards (Render et al.) commonly leave a
trailing newline when a secret is pasted into a multi-line env var box. A key
with a stray \\n fails HTTP header validation outright before ever reaching the
exchange, which is a confusing failure to debug. Settings must strip it."""
from core.settings import Settings


def test_delta_credentials_are_stripped_of_whitespace(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_API_KEY", "mykey123\n")
    monkeypatch.setenv("DELTA_API_SECRET", "  mysecret456  \n")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
mode: PAPER
market: { symbol: BTCUSD, resolution: "5m", lookback: 50 }
loop: { poll_seconds: 30 }
strategy: { name: ny_range_fakeout, params: {} }
risk: { account_equity: 10000.0, risk_per_trade_pct: 1.0, stop_loss_pct: 1.5,
        take_profit_pct: 3.0, max_leverage: 5, max_daily_loss_pct: 5.0 }
logging: { level: INFO, trade_log: logs/trades.csv }
""")
    s = Settings.load(cfg)
    assert s.api_key == "mykey123"
    assert s.api_secret == "mysecret456"
