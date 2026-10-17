# Strategy research and implementation — 2026-10-16

Two candidates were selected for transparent rules, published evidence for their
strategy families, and compatibility with this single-venue futures bot. They
are **not the internet's proven best strategies**, and neither guarantees profit.
Parameters were fixed before running the comparison; no parameter search was
performed. The new candidates were not activated on the running account.

## Evidence and limits

- [Liu & Tsyvinski, Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877)
  reports a time-series momentum effect in its historical cryptocurrency sample.
  This supports investigating directional momentum; it does not validate our
  hourly horizons, filters, stops, or current Delta execution.
- [Moskowitz, Ooi & Pedersen, Time Series Momentum](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum)
  and [Hurst, Ooi & Pedersen, A Century of Evidence on Trend-Following Investing](https://www.aqr.com/insights/research/journal-article/a-century-of-evidence-on-trend-following-investing)
  provide broader historical evidence for trend following. Their markets,
  holding periods, portfolios, and implementations differ from this bot.
- [Corbet et al., The effectiveness of technical trading rules in cryptocurrency markets](https://doras.dcu.ie/25053/)
  studied moving-average and range-breakout rules, with stronger findings for
  moving averages. This is not evidence that our Donchian variant is best.
- [Hudson & Urquhart, Technical trading and cryptocurrencies](https://link.springer.com/article/10.1007/s10479-019-03357-1)
  examined many technical rules, including channel breakouts. Its positive
  historical findings come with a material counterpoint: Bitcoin predictability
  did not survive the paper's out-of-sample period.

These are primary research sources. No influencer win-rate claims were used.
Both selected strategies are trend followers and can lose together in sideways
markets. They do not constitute an independently diversified portfolio.

## Exact implemented rules

### 1. Donchian breakout (`donchian_breakout`)

- Defaults: 1-hour bars; supported alternatives 4h and 1d.
- Long: completed close above the **previous** 55 bars' highest high, above a
  rising EMA200. Short: mirrored conditions below the previous lowest low and
  a falling EMA200. The signal bar is excluded from channel calculations.
- Stop: 2 × ATR14 from the signal close. Target: 3 times the stop distance.
- Strategy exit: long closes below the prior 20-bar low, or short closes above
  the prior 20-bar high. This can exit before the exchange bracket.
- Parameters: `entry_period`, `exit_period`, `trend_period`, `atr_period`,
  `stop_atr`, `reward_r`. Defaults: 55, 20, 200, 14, 2, 3.

### 2. Time-series momentum (`time_series_momentum`)

- Defaults: 1-hour bars; supported alternatives 4h and 1d.
- Long: all price changes across 24, 72, and 168 completed bars exceed +0.5 ATR14,
  with close above EMA200. Short: all changes below −0.5 ATR14 and close below
  EMA200. On hourly data these horizons correspond to 1, 3, and 7 days.
- Stop/target: the same 2 × ATR stop and 3R target.
- Strategy exit: the shortest-horizon change reaches/crosses zero, or close
  crosses the EMA200 against the position. This can exit before the bracket.
- Parameters: `horizons`, `trend_period`, `atr_period`, `min_move_atr`,
  `stop_atr`, `reward_r`. Defaults: [24,72,168], 200, 14, 0.5, 2, 3.

The exact ATR, EMA, horizon, and target choices are engineering defaults, not
parameters proved profitable by the papers. The target distance is a payoff
rule, not a forecast of expected reward. Both strategies reject chasing an entry
more than 0.5 ATR away from the signal close. Sizing and portfolio limits remain
in the shared risk engine. Missing bars and unsupported timeframes block signals.

## Reproduce the comparison

```bash
.venv/bin/python research.py --download --days 180
# Reuse downloaded data (no network):
.venv/bin/python research.py
```

Data: public Delta India perpetual OHLC, downloaded in chunks of at most 1,000
candles per request, without credentials. See the
[Delta historical candle API](https://docs.delta.exchange/#get-historical-ohlc-candles).
The downloader checks contiguous timestamps and the expected total, drops the
unfinished candle, and stores each dataset under `data/research/`. SHA-256 hashes
of the datasets are recorded in `data/strategy_comparison.json`.

Each strategy/market is simulated separately, with $10,000 starting equity,
0.5% planned risk per entry, and a 2× gross leverage limit. The original EMA9/20
strategy is included as a baseline. These settings intentionally differ from
those of the running demo account; these returns are not account forecasts.

Reports include the full sample after a 250-bar warmup and a separate simulation
on the last 30% of bars. Earlier bars supply indicator warmup for that later
simulation; positions and equity are reset at its boundary. This chronological
holdout is a diagnostic, not formal statistical validation. Inspecting it to
choose a market/strategy or to change parameters uses up its independence.

Signals are generated after a bar closes and filled at the **next open**. Costs:
5 basis points fee plus 2 basis points adverse slippage per side, illustrative
assumptions that can be changed with CLI flags. Stops take precedence when both
brackets touch; gap-through stops fill at the opening price. Drawdown is measured
at candle closes, including unrealized P&L and estimated closing fees. Intrabar
drawdown can be larger. Funding, liquidation, available liquidity, and market
impact are not modeled. The separate spot buy-and-hold figure is not risk-matched.

### Observed later-period results

| Market | Donchian net | Momentum net | EMA9/20 baseline net |
| --- | ---: | ---: | ---: |
| BTCUSD | −5.58% | −3.06% | −0.50% |
| ETHUSD | −4.12% | −0.34% | +0.37% |
| SOLUSD | −1.89% | +4.96% | +3.43% |

These figures are from the report produced on 2026-10-16, using the last 30% of
180 days of hourly history. Exact times, trades, drawdown, fees, parameters, and
source hashes are in the JSON report and dashboard Strategy lab. Donchian had
only 20–22 trades per market in this segment. Momentum had 44–57.

**Conclusion:** the test does not establish two profitable strategies. Donchian
lost on all three instruments; momentum was positive only on SOL. Selecting SOL
because of that result would itself introduce selection bias. Forward paper
testing and independent data are needed before treating either as an edge.

## Use in TradeOS

The Strategy lab exposes each candidate with a “Select … · 1h” button. Selection
requires every position to be flat and preserves Paper/Delta Demo and Auto/Manual
mode. It selects the strategy's default hourly timeframe and clears stale chart
and pending-signal state. It does not switch the current bot automatically.

For an isolated paper configuration, set:

```yaml
mode: PAPER
market: { symbol: BTCUSD, resolution: 1h, lookback: 300 }
strategy: { name: donchian_breakout, params: {} }
# Or: strategy: { name: time_series_momentum, params: {} }
```

Replay a plain OHLC JSON array using the shared simulation engine:

```bash
.venv/bin/python run.py --backtest candles.json --symbol BTCUSD \
  --strategy time_series_momentum --resolution 1h --contract-size 0.001 \
  --fee-bps 5 --slippage-bps 2 --report /tmp/momentum.json
```

Changing strategies or restarting while positions exist must not be used as a
way to discard their history or reset loss budgets. Existing production-readiness
limitations documented in README still apply.
