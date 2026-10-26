"""Functional tests for the "BTC Brahmastra" price-action zone-rejection
strategy, ported from a narrated description (no Pine source available).
See strategies/btc_bms.py's module docstring for the judgment calls made on
every ambiguous rule ("identify the trend", "rejection candle", stop/target).

Candle sequences here are built explicitly (not via a generic zigzag
generator) because fractal swing detection is sensitive to exact tie values;
every number below was chosen and verified to avoid incidental ties."""
from strategies.btc_bms import Strategy
from strategies.base import Action


def _c(o, h, l, cl):
    return {"open": o, "high": h, "low": l, "close": cl, "volume": 10}


def _legs(vals_up, vals_down):
    """vals_up/vals_down: lists of price levels; consecutive values become
    one candle each (up candles: low=open; down candles: high=open — this
    keeps each leg's turning point an unambiguous, untied fractal)."""
    out = []
    for vals in vals_up:
        for i in range(len(vals) - 1):
            o, cl = vals[i], vals[i + 1]
            out.append(_c(o, cl + 1, o, cl))
    return out


def _uptrend_to_zone_setup():
    """Two confirmed HH/HL legs (trend='up'), then a 3-green-candle zone,
    a rally away from it (departure), a deep pullback retracing back into
    the zone, and a bullish-engulfing rejection there -> ENTER_LONG."""
    candles = []
    vals = [980, 985, 990, 995, 1000]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1000, 994, 988, 982]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [982, 990, 998, 1006, 1014, 1022]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1022, 1015, 1008, 1001]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    zone_start = len(candles)
    vals = [1001, 1009, 1017, 1025]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    zone_candles = candles[zone_start:]
    zone_low = min(float(x["low"]) for x in zone_candles)
    zone_high = max(float(x["high"]) for x in zone_candles)
    vals = [1025, 1033, 1041, 1049]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1049, 1050, 1051]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 0.5, o, cl))
    vals = [1051, 1040, 1029, 1018]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 0.2, cl))
    return candles, zone_low, zone_high


def test_bullish_rejection_at_a_buy_zone_enters_long():
    candles, zone_low, _ = _uptrend_to_zone_setup()
    prev_o, prev_cl = 1018, 1013
    candles.append(_c(prev_o, prev_o + 0.2, zone_low + 1, prev_cl))     # bearish, dips into zone
    eng_o, eng_cl = 1012, 1020
    candles.append(_c(eng_o, eng_cl + 0.3, zone_low + 0.5, eng_cl))     # bullish, engulfs prev's body
    candles.append(_c(1020, 1023, 1019, 1022))                         # "next candle's close" -> entry

    strat = Strategy(swing_lookback=2)
    strat.required_candles = 10
    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_LONG
    assert sig.stop_loss < candles[-1]["close"] < sig.take_profit
    assert sig.stop_loss < zone_low   # SL sits just past the zone's far edge


def test_holds_when_price_runs_past_the_zone_before_entry_closes():
    # Same bullish-rejection setup as above, but the actual entry candle
    # (one bar after the engulfing/rejection candle) gaps down through the
    # zone's far edge before closing — by the time entry is taken, the
    # computed stop-loss (fixed to the zone) is no longer below entry. A
    # real incident: this used to fire ENTER_LONG anyway, and the risk
    # engine rejected it outright ("stop and target must bracket entry in
    # the trade direction") — a silently lost trade instead of a clean
    # HOLD, and the strategy's own responsibility to never offer an
    # invalid bracket in the first place.
    candles, zone_low, _ = _uptrend_to_zone_setup()
    prev_o, prev_cl = 1018, 1013
    candles.append(_c(prev_o, prev_o + 0.2, zone_low + 1, prev_cl))
    eng_o, eng_cl = 1012, 1020
    candles.append(_c(eng_o, eng_cl + 0.3, zone_low + 0.5, eng_cl))
    collapse = zone_low - 50
    candles.append(_c(1020, 1020, collapse - 1, collapse))

    strat = Strategy(swing_lookback=2)
    strat.required_candles = 10
    sig = strat.generate(candles, None)
    assert sig.action == Action.HOLD
    assert "moved past the zone" in sig.reason


def test_bearish_rejection_at_a_sell_zone_enters_short():
    candles = []
    vals = [1100, 1095, 1090, 1085, 1080]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [1080, 1086, 1092, 1098]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1098, 1090, 1082, 1074, 1066, 1058]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [1058, 1065, 1072, 1079]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    zone_start = len(candles)
    vals = [1079, 1071, 1063, 1055]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    zone_candles = candles[zone_start:]
    zone_high = max(float(x["high"]) for x in zone_candles)
    vals = [1055, 1047, 1039, 1031]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [1031, 1030, 1029]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl - 0.5, o, cl))
    vals = [1029, 1040, 1051, 1062]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 0.2, o, cl))
    prev_o, prev_cl = 1062, 1067
    candles.append(_c(prev_o, zone_high - 1, prev_o - 0.2, prev_cl))    # bullish, pokes into zone
    eng_o, eng_cl = 1068, 1060
    candles.append(_c(eng_o, eng_cl + 0.5, zone_high - 0.5, eng_cl))    # bearish, engulfs prev's body
    candles.append(_c(1060, 1061, 1057, 1058))

    strat = Strategy(swing_lookback=2)
    strat.required_candles = 10
    sig = strat.generate(candles, None)
    assert sig.action == Action.ENTER_SHORT
    assert sig.stop_loss > candles[-1]["close"] > sig.take_profit
    assert sig.stop_loss > zone_high


def test_exits_long_on_a_structure_break_lower_low():
    candles = []
    vals = [980, 985, 990, 995, 1000]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1000, 994, 988, 982]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [982, 990, 998, 1006, 1014, 1022]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 1, o, cl))
    vals = [1022, 1015, 1008, 1001]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    # deep pullback well below the prior swing low (981) -> lower low, confirmed
    vals = [1001, 988, 975, 965]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, o, cl - 1, cl))
    vals = [965, 966, 967]
    for i in range(len(vals) - 1):
        o, cl = vals[i], vals[i + 1]
        candles.append(_c(o, cl + 0.5, o, cl))

    strat = Strategy(swing_lookback=2)
    strat.required_candles = 10
    sig = strat.generate(candles, "long")
    assert sig.action == Action.EXIT
    assert sig.exit_with_bracket is True


def test_holds_without_a_clear_trend():
    candles = []
    p = 1000.0
    for i in range(30):
        p += 1 if i % 2 == 0 else -1
        candles.append(_c(p, p + 2, p - 2, p))
    strat = Strategy(swing_lookback=2)
    strat.required_candles = 10
    assert strat.generate(candles, None).action == Action.HOLD


def test_holds_while_warming_up():
    candles = [_c(1000, 1001, 999, 1000) for _ in range(5)]
    strat = Strategy()
    assert strat.generate(candles, None).action == Action.HOLD
