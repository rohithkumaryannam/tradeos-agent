# TradeOS — multi-market trading workspace

A Python trading engine and browser dashboard for **paper trading and Delta
Exchange testnet**. BTC, ETH, and SOL share one account-level risk budget, with
independent strategy instances and positions. Production trading is not enabled.

## Run

```bash
pip install -r requirements.txt
python server.py
# http://127.0.0.1:8000
```

Start in **Paper**. Choose a strategy, then use **Run one cycle** or **Start Bot**.
For Delta Demo, configure testnet credentials using `.env.example`. Credentials
stay on the server. Set `TRADEOS_TOKEN` when deploying beyond localhost. Run a
MongoDB is recommended when more than one instance can run: TradeOS uses an
atomic lease so exactly one Delta Demo instance can execute while the others
remain read-only. Set `TRADEOS_EXECUTION_ROLE=read_only` to force a monitor.

## Workspace

- Account equity, realized daily P&L, unrealized P&L, and gross exposure.
- Selectable markets with candlestick, EMA, RSI, position, and protection views.
- Portfolio stop-risk and leverage utilization, position slots, daily loss budget.
- Per-market candle freshness, latest decision, execution issues, and cycle timing.
- Trade journal with market filters and CSV export of the displayed history
  (up to 100 completed trades). Performance covers up to 500 completed trades.
- Manual signal confirmation with expiration; visible errors for failed controls.
- Optional Gemini commentary and analysis; AI does not place orders.

**Pause new entries** keeps position management active while the loop is running.
**Stop** stops the whole loop after its current cycle, including local protection.
Exchange-confirmed brackets run on Delta independently. The dashboard's feed
indicator describes its WebSocket connection; market cards separately report
candle freshness. Pause state and desired run state survive server restarts.

## Risk and execution

Configure `risk` in `config.yaml`:

| Setting | Meaning |
| --- | --- |
| `risk_per_trade_pct` | Maximum entry-to-stop risk for one trade |
| `max_portfolio_risk_pct` | Combined entry-to-stop risk budget; default 3% |
| `max_open_positions` | Maximum concurrent positions; default 3 |
| `max_leverage` | Combined gross notional / equity ceiling |
| `max_daily_loss_pct` | Realized loss cap, resetting at UTC midnight |
| `stop_loss_pct`, `take_profit_pct` | Default bracket distances |
| `max_consecutive_losses` | Pause one strategy after this many consecutive losses |
| `strategy_cooldown_minutes` | Duration of that strategy pause |

Sizing uses the remaining portfolio budgets. Non-finite prices, incorrect
bracket direction, partial brackets, and unknown contract specifications reject
entries. Entry-to-stop risk is a sizing estimate; gaps, fees, funding, slippage,
and unrealized losses are not included in that budget.

Stale candles and expired manual signals cannot open positions. Failed account
or position reads block Delta entries. Unreconciled positions, missing exchange
protection, or unresolved order submissions also block new entries. A market's
evaluation failure does not skip other markets' exit management. Repeated polls
cannot re-enter the same market on the same candle.

Delta entries carry a unique `client_order_id`. An entry failure is **not retried
as an unprotected market order**: the broker latches an execution issue. Verify
the order and any position on Delta before restarting; this latch is currently
process-local. An accepted entry is retained locally even if the subsequent
bracket-verification request fails. Startup reconciliation adopts existing
positions; failed reconciliation blocks entries. Order fields follow the
[Delta API reference](https://docs.delta.exchange/).

Dashboard risk edits are explicit runtime overrides stored in MongoDB/local
state. The Risk page names the active source, lists differences from
`config.yaml`, and can reset the override. The configured paper shadow lane
runs the same market inputs through an independent simulated broker and reports
its results under System Health.

Use `/healthz` for uptime monitoring. It requires no dashboard cookie and
returns HTTP 503 when the requested bot loop, Delta account, or configured
MongoDB dependency is unhealthy. `/login` only checks the web server.
Optional webhook/Slack, Telegram, and SMTP settings are documented in
`.env.example`; alerts cover entries, exits, account failures, risk pauses, and
position reconciliation changes.

## Offline strategy replay

```bash
python run.py --backtest data/synthetic.json --symbol BTCUSD \
  --contract-size 0.001 --fee-bps 5 --report /tmp/tradeos-report.json
```

Replay always forces paper mode **before agent construction**, clears credentials,
disables AI, uses a client without order methods, and processes one instrument.
Set the contract size for the instrument represented by your JSON file. Input is
an ascending array of candles containing `time` (Unix seconds), `open`, `high`,
`low`, and `close`, with more than 30 observations.

A replay clock drives signal expiry, journal timestamps, and UTC loss-cap resets.
Signals use completed bars and fill at the next bar open; stops take precedence
if a candle hits both brackets. Gap-through stops fill at the opening price. Fees are charged on both entry and exit notionals when closing.
The default 5 basis points per side is an illustrative assumption, not an exchange
fee quote. Replay logs go to `logs/backtest.csv`, separate from normal trading.
Each report excludes earlier replay history. Replay analytics retain all trades in that run; the dashboard history is bounded
separately.

The bundled synthetic data is a plumbing check and may produce no trades. Replay
models configurable adverse slippage (default 2 bps per fill), but not liquidity,
funding, or exact exchange fill behavior; historical
results do not establish future profitability.

## Strategy research lab

Two configurable candidates are installed: **Donchian breakout** and
**time-series momentum**. Their dashboard cards show fixed-rule historical
comparisons against the EMA baseline, including losses and drawdown. Selecting
one requires flat positions and changes its timeframe to 1h. The running
strategy is never changed by generating a report.

```bash
python research.py --download --days 180
```

Read [the sources, exact rules, costs, and measured results](docs/strategy-research.md).
The initial comparison did **not** establish two profitable strategies. These
remain experimental paper/testnet candidates.

## Strategies and development

`strategies/ema_crossover.py` implements the EMA 9/20 crossover, filtered by
an EMA200 trend (only takes a cross in the direction of the longer trend).
It's currently the only active strategy. Implement `BaseStrategy.generate()`
returning a `Signal` to add another one. Risk and execution stay in the
shared engine. Strategy changes require flat positions and clear pending signals.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
node --check web/app.js
python scripts/check_secrets.py
```

Tests block outbound HTTP, disable AI, and isolate runtime-state files. They cover
portfolio limits, invalid prices, stale data, manual expiry, pause behavior,
ambiguous submissions, startup reconciliation, replay, authentication, and runner
concurrency. The UI uses vanilla JavaScript and canvas; there is no build step.

## Before production use

This remains a paper/testnet system. Exact fill/partial-fill reconciliation,
fee/funding accounting for exchange positions, durable order-intent recovery,
and persistent daily risk/equity state still need work. Exchange exit P&L may use
a candle price estimate. The daily loss cap resets on process restart, and the
legacy CSV journal does not distinguish paper trades from demo trades. Evaluate
strategies on independent historical data and forward-test on testnet before
considering a separate production integration.
