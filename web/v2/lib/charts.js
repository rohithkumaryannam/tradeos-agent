// TradingView lightweight-charts wrappers (loaded globally from the CDN in
// index.html). Times are shifted to the viewer's local zone unless the UTC
// preference is on, because the library renders timestamps as UTC.
import { html, useEffect, useRef, useState } from "./preact.js";
import { api, cssVar as rawVar, prefs, fmtPrice, fmtUsd, parseTs } from "./util.js";

// lightweight-charts only parses hex/rgb(a); normalise any CSS colour
// (our theme uses hsl() for the accent) through a canvas context.
const colorCtx = document.createElement("canvas").getContext("2d");
function cssVar(name) {
  const raw = rawVar(name);
  if (!raw || !colorCtx) return raw;
  colorCtx.fillStyle = "#000";
  colorCtx.fillStyle = raw;
  return colorCtx.fillStyle;
}

function lw() { return window.LightweightCharts; }

export function waitForCharts(timeout = 10000) {
  return new Promise((resolve) => {
    if (lw()) return resolve(true);
    const started = Date.now();
    const t = setInterval(() => {
      if (lw()) { clearInterval(t); resolve(true); }
      else if (Date.now() - started > timeout) { clearInterval(t); resolve(false); }
    }, 80);
  });
}

export function tzShift() {
  return prefs.get().utc ? 0 : -new Date().getTimezoneOffset() * 60;
}

function baseOptions() {
  const text = cssVar("--text-3") || "#888";
  const grid = cssVar("--chart-grid") || "rgba(128,128,128,.1)";
  const border = cssVar("--border-2") || "rgba(128,128,128,.2)";
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: text, fontFamily: "Inter, system-ui, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { borderColor: border, scaleMargins: { top: 0.1, bottom: 0.08 } },
    timeScale: { borderColor: border, timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 8 },
    crosshair: {
      mode: 0,
      vertLine: { color: cssVar("--border-3"), labelBackgroundColor: cssVar("--surface-3") },
      horzLine: { color: cssVar("--border-3"), labelBackgroundColor: cssVar("--surface-3") },
    },
    handleScale: { axisPressedMouseMove: true },
    localization: { priceFormatter: (p) => fmtPrice(p) },
  };
}

function useThemeVersion() {
  const [v, setV] = useState(0);
  useEffect(() => prefs.subscribe(() => setV((x) => x + 1)), []);
  return v;
}

/**
 * Candlestick chart for one market: candles + volume + EMAs from /api/chart,
 * live last-bar updates, the open position's entry/SL/TP lines and the
 * journal's entries/exits as markers.
 */
export function CandleChart({ symbol, tf, livePrice, position, trades, indicators, height = 470, onLegend }) {
  const box = useRef(null);
  const chartRef = useRef(null);
  const series = useRef({});
  const lines = useRef([]);
  const dataRef = useRef([]);
  const posRef = useRef(position);
  posRef.current = position;
  const [state, setState] = useState("loading");
  const [ready, setReady] = useState(false);
  const theme = useThemeVersion();
  const ind = indicators || { ema9: true, ema20: true, ema200: false, volume: true };

  // create once
  useEffect(() => {
    let disposed = false;
    waitForCharts().then((ok) => {
      if (disposed) return;
      if (!ok || !box.current) { setState("nolib"); return; }
      const L = lw();
      const chart = L.createChart(box.current, baseOptions());
      const up = cssVar("--up"), down = cssVar("--down");
      series.current.candles = chart.addCandlestickSeries({
        upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
        priceLineColor: cssVar("--accent"), priceLineStyle: 2,
        // keep the open position's stop and target on screen
        autoscaleInfoProvider: (original) => {
          const res = original();
          const p = posRef.current;
          if (!res || !res.priceRange || !p) return res;
          const levels = [p.sl, p.tp, p.entry].map(Number).filter(Number.isFinite);
          if (!levels.length) return res;
          return { ...res, priceRange: { minValue: Math.min(res.priceRange.minValue, ...levels), maxValue: Math.max(res.priceRange.maxValue, ...levels) } };
        },
      });
      series.current.volume = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
      const emaOpts = (color, w = 1.5) => ({ color, lineWidth: w, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      series.current.ema9 = chart.addLineSeries(emaOpts("#4aa8ff"));
      series.current.ema20 = chart.addLineSeries(emaOpts("#f5a524"));
      series.current.ema200 = chart.addLineSeries(emaOpts("#b48cff", 2));
      chart.subscribeCrosshairMove((p) => {
        if (!onLegend) return;
        const bar = p?.seriesData?.get?.(series.current.candles);
        onLegend(bar || null);
      });
      chartRef.current = chart;
      setReady(true);
    });
    return () => {
      disposed = true;
      try { chartRef.current?.remove(); } catch { /* already gone */ }
      chartRef.current = null; series.current = {}; lines.current = [];
    };
  }, []);

  // theme
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const o = baseOptions(); delete o.autoSize;
    chart.applyOptions(o);
    const up = cssVar("--up"), down = cssVar("--down");
    series.current.candles?.applyOptions({ upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down, priceLineColor: cssVar("--accent") });
  }, [theme, ready]);

  // data
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setState("loading");
    const load = async (first) => {
      try {
        const r = await api(`/api/chart?symbol=${encodeURIComponent(symbol)}&resolution=${encodeURIComponent(tf)}`);
        if (cancelled || !chartRef.current) return;
        const shift = tzShift();
        const rows = (r.chart || []).filter((c) => c.t);
        dataRef.current = rows.map((c) => ({ time: c.t + shift, open: c.o, high: c.h, low: c.l, close: c.c }));
        const s = series.current;
        s.candles.setData(dataRef.current);
        const up = cssVar("--up-line"), down = cssVar("--down-line");
        s.volume.setData(rows.map((c) => ({ time: c.t + shift, value: c.volume || 0, color: c.c >= c.o ? up : down })));
        s.ema9.setData(rows.filter((c) => c.ema_fast != null).map((c) => ({ time: c.t + shift, value: c.ema_fast })));
        s.ema20.setData(rows.filter((c) => c.ema_slow != null).map((c) => ({ time: c.t + shift, value: c.ema_slow })));
        s.ema200.setData(rows.filter((c) => c.ema_trend != null).map((c) => ({ time: c.t + shift, value: c.ema_trend })));
        if (first) chartRef.current.timeScale().fitContent();
        setState(rows.length ? "ready" : "empty");
        applyMarkers();
      } catch (e) {
        if (!cancelled) setState(dataRef.current.length ? "ready" : "error");
      }
    };
    load(true);
    const t = setInterval(() => load(false), 20000);
    return () => { cancelled = true; clearInterval(t); };
  }, [symbol, tf, ready]);

  // indicator visibility
  useEffect(() => {
    const s = series.current;
    if (!s.candles) return;
    s.ema9.applyOptions({ visible: !!ind.ema9 });
    s.ema20.applyOptions({ visible: !!ind.ema20 });
    s.ema200.applyOptions({ visible: !!ind.ema200 });
    s.volume.applyOptions({ visible: !!ind.volume });
  }, [ind.ema9, ind.ema20, ind.ema200, ind.volume, ready]);

  // live last bar
  useEffect(() => {
    const s = series.current;
    const data = dataRef.current;
    if (!s.candles || !data.length || !Number.isFinite(Number(livePrice))) return;
    const lastBar = data[data.length - 1];
    const p = Number(livePrice);
    const bar = { ...lastBar, close: p, high: Math.max(lastBar.high, p), low: Math.min(lastBar.low, p) };
    data[data.length - 1] = bar;
    try { s.candles.update(bar); } catch { /* out-of-order during reload */ }
  }, [livePrice]);

  // position lines
  useEffect(() => {
    const c = series.current.candles;
    if (!c) return;
    lines.current.forEach((l) => { try { c.removePriceLine(l); } catch { /* ignore */ } });
    lines.current = [];
    if (!position) return;
    const mk = (price, color, title, style = 0) => price && lines.current.push(c.createPriceLine({ price: Number(price), color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    mk(position.entry, cssVar("--accent"), `${position.side === "long" ? "LONG" : "SHORT"} ${position.size}`, 0);
    mk(position.sl, cssVar("--down"), "SL", 2);
    mk(position.tp, cssVar("--up"), "TP", 2);
  }, [position?.entry, position?.sl, position?.tp, position?.side, ready, theme]);

  function applyMarkers() {
    const c = series.current.candles;
    const data = dataRef.current;
    if (!c || !data.length) return;
    const shift = tzShift();
    const t0 = data[0].time, t1 = data[data.length - 1].time;
    const step = data.length > 1 ? data[1].time - data[0].time : 900;
    const snap = (ts) => { const t = Math.floor(ts / 1000) + shift; return t - ((t - t0) % step); };
    const ms = [];
    for (const tr of trades || []) {
      const en = parseTs(tr.entry_ts), ex = parseTs(tr.exit_ts);
      if (en) {
        const t = snap(en);
        if (t >= t0 && t <= t1) ms.push({ time: t, position: tr.side === "long" ? "belowBar" : "aboveBar", color: tr.side === "long" ? cssVar("--up") : cssVar("--down"), shape: tr.side === "long" ? "arrowUp" : "arrowDown", text: tr.side === "long" ? "L" : "S" });
      }
      if (ex) {
        const t = snap(ex);
        if (t >= t0 && t <= t1) ms.push({ time: t, position: tr.side === "long" ? "aboveBar" : "belowBar", color: tr.pnl >= 0 ? cssVar("--up") : cssVar("--down"), shape: "circle", text: fmtUsd(tr.pnl, { sign: true }) });
      }
    }
    if (position?.entry_ts) {
      const t = snap(parseTs(position.entry_ts));
      if (t >= t0 && t <= t1) ms.push({ time: t, position: position.side === "long" ? "belowBar" : "aboveBar", color: cssVar("--accent"), shape: position.side === "long" ? "arrowUp" : "arrowDown", text: "open" });
    }
    ms.sort((a, b) => a.time - b.time);
    try { c.setMarkers(ms); } catch { /* ignore */ }
  }
  useEffect(applyMarkers, [trades, position?.entry_ts, state, ready]);

  return html`<div class="chart-box" style=${{ height: height + "px" }}>
    <div ref=${box} style="position:absolute;inset:0"></div>
    ${state === "loading" && html`<div class="chart-msg"><span class="spinner"></span></div>`}
    ${state === "nolib" && html`<div class="chart-msg">Charts couldn't load (CDN unreachable). <a href="">Retry</a></div>`}
    ${state === "error" && html`<div class="chart-msg">Chart feed unavailable — retrying…</div>`}
    ${state === "empty" && html`<div class="chart-msg">No candles for ${symbol} on ${tf}</div>`}
  </div>`;
}

/** Line/area chart for equity or cumulative P&L. data: [{time: unix-seconds, value}] */
export function AreaChart(props) {
  if (!props.data || props.data.length < 2) {
    return html`<div class="chart-box" style=${{ height: (props.height || 240) + "px" }}>
      <div class="chart-msg"><div class="empty" style="padding:0"><div class="e-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8M15 7h6v6"/></svg></div>
      <span>${props.empty || "Not enough data yet — this fills in as trades close."}</span></div></div></div>`;
  }
  return html`<${AreaChartInner} ...${props} />`;
}

function AreaChartInner({ data, height = 240, baseline = false, tone, valueFormat = (v) => fmtUsd(v) }) {
  const box = useRef(null);
  const chartRef = useRef(null);
  const ser = useRef(null);
  const theme = useThemeVersion();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let disposed = false;
    waitForCharts().then((ok) => {
      if (disposed || !ok || !box.current) return;
      const chart = lw().createChart(box.current, {
        ...baseOptions(),
        rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.06 } },
        timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
        localization: { priceFormatter: valueFormat },
        handleScroll: false, handleScale: false,
      });
      chartRef.current = chart;
      setReady(true);
    });
    return () => { disposed = true; try { chartRef.current?.remove(); } catch { /* ignore */ } chartRef.current = null; ser.current = null; };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const o = baseOptions(); delete o.autoSize;
    chart.applyOptions({ layout: o.layout, grid: { vertLines: { visible: false }, horzLines: { color: o.grid.horzLines.color } }, crosshair: o.crosshair });
    if (ser.current) { try { chart.removeSeries(ser.current); } catch { /* ignore */ } ser.current = null; }
    const up = cssVar("--up"), down = cssVar("--down"), acc = cssVar("--accent");
    if (baseline) {
      ser.current = chart.addBaselineSeries({
        baseValue: { type: "price", price: 0 }, lineWidth: 2,
        topLineColor: up, topFillColor1: hexA(up, 0.28), topFillColor2: hexA(up, 0.02),
        bottomLineColor: down, bottomFillColor1: hexA(down, 0.02), bottomFillColor2: hexA(down, 0.28),
        priceLineVisible: false,
      });
    } else {
      const col = tone === "down" ? down : tone === "up" ? up : acc;
      ser.current = chart.addAreaSeries({ lineColor: col, topColor: hexA(col, 0.3), bottomColor: hexA(col, 0.01), lineWidth: 2, priceLineVisible: false });
    }
    draw();
  }, [ready, theme, baseline, tone]);

  function draw() {
    if (!ser.current) return;
    const shift = tzShift();
    const seen = new Set();
    const rows = (data || []).filter((d) => Number.isFinite(d.time) && Number.isFinite(d.value))
      .map((d) => ({ time: Math.floor(d.time) + shift, value: d.value }))
      .sort((a, b) => a.time - b.time)
      .filter((d) => { if (seen.has(d.time)) return false; seen.add(d.time); return true; });
    ser.current.setData(rows);
    chartRef.current?.timeScale().fitContent();
  }
  useEffect(draw, [data]);

  return html`<div class="chart-box" style=${{ height: height + "px" }}>
    <div ref=${box} style="position:absolute;inset:0"></div>
  </div>`;
}

function hexA(color, a) {
  const c = String(color).trim();
  if (c.startsWith("#") && (c.length === 7 || c.length === 4)) {
    const full = c.length === 4 ? "#" + [...c.slice(1)].map((x) => x + x).join("") : c;
    const n = parseInt(full.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  if (c.startsWith("rgb(")) return c.replace("rgb(", "rgba(").replace(")", `, ${a})`);
  return c;
}
