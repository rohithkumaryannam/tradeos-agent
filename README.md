# TradeOS — BTC Futures Trading Bot + Dashboard

An autonomous BTC perpetual-futures trading bot for **Delta Exchange** with a
live **web dashboard**. It pulls candles, analyzes the chart (EMA + RSI), sizes
trades through a risk engine, and takes **entries and exits automatically** — in
paper mode (simulated) or on the Delta **demo/testnet** (real orders, play money).

Everything is controllable from the dashboard: **Paper ⇄ Delta Demo**,
**Auto ⇄ Manual** execution, start/stop, and one-click confirm of pending trades.

![layout](docs-screenshot-placeholder)

---

## Quick start — the dashboard

```bash
pip install -r requirements.txt
python server.py
# open http://127.0.0.1:8000
```

Then: pick **Paper**, click **Start Bot**. The chart, equity curve, position
panel, risk status, trade history and live feed all update every second.

For **Delta Demo** (real testnet orders): `cp .env.example .env`, paste your
testnet API key/secret, then flip the mode toggle to **Delta Demo** in the UI.
Get testnet keys at https://testnet.delta.exchange → Settings → API Keys.

### Headless / CLI (no dashboard)

```bash
python run.py --backtest data/synthetic.json   # replay test candles
python run.py --once                            # one live cycle
python run.py                                   # run the loop forever
```

---

## How it works

```
run.py ─► Agent.step() every poll_seconds:
          1. fetch candles          (core/delta_client.py)
          2. open position? check SL/TP → close + book PnL   (core/broker.py)
          3. strategy signal         (strategies/<name>.py)
          4. ENTER → risk engine sizes it → broker opens      (core/risk.py)
          5. EXIT  → broker closes → book PnL
          every action is written to logs/trades.csv
```

| File | Role |
|---|---|
| `config.yaml` | all tunables — mode, symbol, timeframe, strategy, risk |
| `.env` | Delta API key/secret (never commit) |
| `core/settings.py` | loads config + env |
| `core/delta_client.py` | signed Delta REST client (candles, orders, positions, balance) |
| `core/indicators.py` | EMA / RSI / ATR, pure Python |
| `strategies/base.py` | `BaseStrategy` + `Signal` interface |
| `strategies/ema_crossover.py` | **placeholder** strategy — replace |
| `core/risk.py` | sizing, SL/TP, leverage cap, daily loss cap |
| `core/broker.py` | `PaperBroker` (sim) and `DeltaBroker` (testnet) |
| `core/agent.py` | decision + execution engine, state snapshot for the UI |
| `strategies/confluence.py` | **built-in** EMA+RSI pullback strategy (default) |
| `server.py` | FastAPI backend: REST control + WebSocket live push |
| `web/index.html`, `web/app.js` | the dashboard (canvas charts, no build step) |
| `run.py` | headless entrypoint (live / `--once` / `--backtest`) |

## Dashboard features

- **Candlestick chart** with EMA 9 / 21 overlay and last-price label
- **RSI 14** strip with 30/50/70 guides
- **Equity curve** (filled area)
- **Open position** card — side, entry, size, SL, TP, notional, live unrealized P&L
- **Risk panel** — all four controls + daily-cap status (OK / HALTED)
- **Trade history** table and a **live event feed**
- **Controls** — Start/Stop, Paper⇄Delta Demo, Auto⇄Manual, Confirm/Dismiss pending, Close position

## Execution modes

- **Auto** — the bot opens and closes positions itself; the dashboard monitors.
- **Manual** — a signal appears as a highlighted **pending** banner; you click
  **Confirm Trade** to place it or **Dismiss** to skip. Stop-loss and take-profit
  always execute automatically even in manual mode, so a position is never left
  unprotected.

---

## Writing your own strategy

1. Copy `strategies/ema_crossover.py` to `strategies/my_strategy.py`.
2. Set `name = "my_strategy"` and implement `generate()`:

```python
from core.indicators import rsi, closes
from strategies.base import BaseStrategy, Signal, Action

name = "my_strategy"

class Strategy(BaseStrategy):
    def __init__(self, period=14, **kw):
        super().__init__(period=period, **kw); self.period = period

    def generate(self, candles, position_side):
        r = rsi(closes(candles), self.period)
        if r[-1] is None:
            return Signal(Action.HOLD, "warming up")
        if position_side is None and r[-1] < 30:
            return Signal(Action.ENTER_LONG, f"RSI {r[-1]:.0f} oversold")
        if position_side == "long" and r[-1] > 70:
            return Signal(Action.EXIT, f"RSI {r[-1]:.0f} overbought")
        return Signal(Action.HOLD, "")
```

3. Point `config.yaml` at it:

```yaml
strategy:
  name: my_strategy
  params: { period: 14 }
```

The strategy only decides **direction**. Position size, stop-loss, take-profit,
leverage and the daily loss cap are all enforced by the risk engine from
`config.yaml` — you never hard-code those in the strategy.

---

## Risk controls (all in `config.yaml` → `risk:`)

- **`risk_per_trade_pct`** — each trade is sized so the stop-loss costs this % of equity.
- **`stop_loss_pct` / `take_profit_pct`** — bracket distance from entry.
- **`max_leverage`** — size is shrunk to stay under this. Hard cap.
- **`max_daily_loss_pct`** — after this much realized loss in a day, the agent
  stops opening trades until the next day.

---

## Safety notes

- Default mode is `PAPER` — nothing touches an exchange.
- `DELTA_DEMO` uses the **testnet** base URL and testnet keys only. No real funds.
- Before pointing this at live markets (not recommended without thorough testing),
  you'd change the base URL in `core/delta_client.py` and use production keys —
  do so at your own risk. This is a tool, not financial advice.
- Verify Delta's current bracket-order fields in `core/broker.py::DeltaBroker.open`
  against the live API docs; the SL/TP bracket wiring is marked for you to confirm.
```
