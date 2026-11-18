"""Offline, single-instrument replay. Signals at close; fills next bar open."""
import math

from core import db
from core.agent import _RES_SECONDS
from strategies.base import Action


def validate_candles(candles, seconds):
    previous = None
    for b in candles:
        stamp, o, h, l, c = (float(b[k]) for k in ("time", "open", "high", "low", "close"))
        if not all(math.isfinite(v) for v in (stamp, o, h, l, c)) or min(o, h, l, c) <= 0:
            raise ValueError("non-finite or non-positive OHLC data")
        if not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError("invalid OHLC range")
        if previous is not None and stamp - previous != seconds:
            raise ValueError("candles must be ordered, unique, and continuous at the configured resolution")
        previous = stamp


def simulate(agent, candles, warmup=30, fee_bps=5, slippage_bps=2, *,
             bracket_owns_exit=False, progress=None):
    """Replay `candles` through `agent` (see module docstring).

    bracket_owns_exit=True reproduces live execution, where a confirmed
    exchange bracket makes the agent ignore a strategy's own EXIT signal
    unless that signal sets exit_with_bracket (core/agent.py), so only the
    stop-loss / take-profit close trades. The default (False) keeps the
    historical research behaviour of honouring every strategy exit.

    progress, if given, is called as progress(done_bars, total_bars).

    Always runs with the database switched off for this thread: replays use
    real symbol names, and must never read or write the live bot's
    position brackets, trade log or state."""
    with db.offline():
        return _simulate(agent, candles, warmup, fee_bps, slippage_bps,
                         bracket_owns_exit, progress)


def _simulate(agent, candles, warmup, fee_bps, slippage_bps, bracket_owns_exit, progress):
    # This marker is set only by run.ReplayClient, never a DeltaClient.
    if agent.s.mode != "PAPER" or not getattr(agent.client, "offline_replay", False):
        raise ValueError("backtest requires PAPER mode and an offline replay client")
    # A replay can write thousands of rows in seconds — never mirror them
    # into the shared live Mongo collection (it polluted the real trade
    # journal before this guard existed).
    agent.tlog.mirror = False
    if len(agent.markets) != 1 or len(candles) <= warmup + 1:
        raise ValueError("backtest needs one symbol and sufficient candles after warmup")
    if any(not math.isfinite(v) or v < 0 or v >= 10_000 for v in (fee_bps, slippage_bps)):
        raise ValueError("cost assumptions must be finite and between 0 and 10,000 bps")
    seconds = _RES_SECONDS[agent.s.resolution]
    validate_candles(candles, seconds)
    m = next(iter(agent.markets.values()))
    allowed = getattr(m.strategy, "supported_resolutions", None)
    if allowed and agent.s.resolution not in allowed:
        raise ValueError("strategy requires 1h, 4h or 1d candles")
    required = getattr(m.strategy, "required_candles", 1)
    if agent.s.lookback < required:
        raise ValueError(f"lookback must be at least {required}")
    if m.broker.position or agent.trades:
        # Logs from previous runs may exist; only a fresh equity/position is used.
        if m.broker.position or agent.risk.equity != agent.s.risk.account_equity:
            raise ValueError("use a fresh agent for every replay")
    agent.trades = []
    agent.trade_history_limit = len(candles)
    agent.s.ai_narrate = False
    m.broker.fee_bps = fee_bps
    now = [float(candles[0]["time"])]
    agent.clock = agent.risk.clock = lambda: now[0]
    agent.equity_curve = [{"t": agent._now(), "equity": agent.risk.equity}]
    start_equity = peak = agent.risk.equity
    max_drawdown = fees = slippage = 0.0
    rejected = 0
    pending = None
    first = max(warmup, required - 1)
    if first + 1 >= len(candles):
        raise ValueError("insufficient candles after strategy warmup")

    def close(price, reason):
        nonlocal fees, slippage
        pos = m.broker.position
        direction = 1 if pos.side == "long" else -1
        fill = price * (1 - direction * slippage_bps / 10_000)
        units = pos.notional / pos.entry
        fees += (pos.notional + units * fill) * fee_bps / 10_000
        slippage += abs(fill - price) * units
        agent._do_exit(m, fill, reason)

    def mark(price):
        nonlocal peak, max_drawdown
        equity = agent.risk.equity
        p = m.broker.position
        if p:
            equity += m.broker._pnl(price)
            equity -= (p.notional + p.notional / p.entry * price) * fee_bps / 10_000
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        return equity

    total = len(candles) - first
    sample_every = max(1, total // 400)
    report_every = max(1, total // 50)
    samples = []
    for i in range(first, len(candles)):
        bar = candles[i]
        now[0] = float(bar["time"])
        # Pending signal was made using ONLY bars through i-1.
        if pending:
            if (pending.action == Action.EXIT and m.broker.position
                    and not (bracket_owns_exit and not getattr(pending, "exit_with_bracket", False))):
                close(float(bar["open"]), pending.reason)
            elif pending.action in (Action.ENTER_LONG, Action.ENTER_SHORT) and not m.broker.position:
                direction = 1 if pending.action == Action.ENTER_LONG else -1
                price = float(bar["open"])
                fill = price * (1 + direction * slippage_bps / 10_000)
                outcome = agent._open_from_signal(m, pending, fill)
                if outcome.event == "entry":
                    slippage += abs(fill - price) * m.broker.position.notional / fill
                else:
                    rejected += 1
        pending = None
        now[0] += seconds
        # A bracket may trigger during the fill bar; worst-case stop first
        # when both extremes touch. Gap-through stops fill at the opening price.
        if m.broker.position:
            hit = m.broker.check_bracket(bar)
            if hit:
                close(hit[1], hit[2])
        m.last_candles = candles[max(0, i + 1 - agent.s.lookback):i + 1]
        m.last_fetch = now[0]
        equity_now = mark(float(bar["close"]))
        done = i - first + 1
        if done % sample_every == 0 or done == total:
            samples.append({"t": int(float(bar["time"])), "equity": round(equity_now, 4)})
        if progress and (done % report_every == 0 or done == total):
            progress(done, total)
        side = m.broker.position.side if m.broker.position else None
        pending = m.strategy.generate(m.last_candles, side)

    if m.broker.position:
        close(float(candles[-1]["close"]), "backtest end")
        mark(float(candles[-1]["close"]))
    final = agent.risk.equity
    first_price = float(candles[first+1]["open"])
    return {
        "strategy": agent.s.strategy_name, "symbol": m.symbol,
        "resolution": agent.s.resolution, "candles": len(candles),
        "evaluation_start": candles[first+1]["time"],
        "evaluation_end": candles[-1]["time"] + seconds,
        "starting_equity": start_equity, "final_equity": round(final, 6),
        "return_pct": round((final / start_equity - 1) * 100, 3),
        "max_drawdown_pct": round(max_drawdown, 3),
        "buy_hold_spot_return_pct": round((float(candles[-1]["close"]) / first_price - 1) * 100, 3),
        "fee_bps_per_side": fee_bps, "slippage_bps_per_side": slippage_bps,
        "fees_paid": round(fees, 4), "slippage_cost": round(slippage, 4),
        "rejected_entries": rejected, "analytics": agent._analytics(),
        "bracket_owns_exit": bool(bracket_owns_exit), "equity_samples": samples,
        "assumptions": "Closed-bar signals, next-open fills; adverse slippage on every fill; stop-first if both brackets touch; gap-through stops at open. Drawdown sampled at closes. Funding, liquidation and liquidity not modeled. Buy-and-hold is unlevered spot before costs, not a risk-matched benchmark. Research only, not proof of future profit.",
    }
