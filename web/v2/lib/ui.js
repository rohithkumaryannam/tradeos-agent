// Shared UI building blocks.
import { html, useState, useEffect, useRef, useMemo } from "./preact.js";
import { Icon } from "./icons.js";
import { coinMeta, fmtPrice, fmtUsd, tone, timeAgo, isNum, clamp } from "./util.js";
import { openModal } from "./store.js";

export function cx(...parts) { return parts.filter(Boolean).join(" "); }

export function Button({ variant = "", size = "", icon, iconRight, loading, children, class: cls, block, ...rest }) {
  return html`<button type="button" class=${cx("btn", variant, size, block && "block", !children && icon && "icon-only", cls)}
      disabled=${rest.disabled || loading} ...${rest}>
    ${loading ? html`<span class="spinner"></span>` : icon && html`<${Icon} name=${icon} size=${size === "sm" ? 15 : 17} />`}
    ${children}
    ${iconRight && html`<${Icon} name=${iconRight} size=${15} />`}
  </button>`;
}

export function Badge({ tone: t = "", dot, pulse, children, class: cls, lg, title }) {
  return html`<span class=${cx("badge", t, lg && "lg", cls)} title=${title}>${dot && html`<span class=${cx("dot", pulse && "pulse")}></span>`}${children}</span>`;
}

export function Card({ title, sub, actions, children, pad, class: cls, flush, foot, icon, glow, ...rest }) {
  return html`<section class=${cx("card", pad && "pad", glow && "glow", cls)} ...${rest}>
    ${(title || actions) && html`<div class="card-head">
      ${icon && html`<${Icon} name=${icon} size=${17} class="faint" />`}
      <div class="grow" style="min-width:0"><h3 class="ellipsis">${title}</h3>${sub && html`<div class="sub ellipsis">${sub}</div>`}</div>
      ${actions && html`<div class="actions">${actions}</div>`}
    </div>`}
    ${title || actions ? html`<div class=${cx("card-body", flush && "flush")}>${children}</div>` : children}
    ${foot && html`<div class="card-foot">${foot}</div>`}
  </section>`;
}

export function Stat({ label, value, sub, valueClass, icon, spark, sparkTone, children, title }) {
  return html`<div class=${cx("card stat", spark && "has-spark")} title=${title}>
    <div class="s-label">${icon && html`<${Icon} name=${icon} size=${15} class="faint" />`}${label}</div>
    <div class=${cx("s-value", valueClass)}>${value}</div>
    ${sub && html`<div class="s-sub">${sub}</div>`}
    ${children}
    ${spark && spark.length > 1 && html`<div class="s-spark"><${Spark} values=${spark} tone=${sparkTone} fill /></div>`}
  </div>`;
}

export function Toggle({ checked, onChange, label, disabled, tone: t, title }) {
  return html`<label class=${cx("toggle", t)} title=${title}>
    <input type="checkbox" checked=${!!checked} disabled=${disabled} onChange=${(e) => onChange?.(e.currentTarget.checked)} />
    <span class="track"></span>${label && html`<span class="t-label">${label}</span>`}
  </label>`;
}

export function Seg({ options, value, onChange, size, block, class: cls }) {
  return html`<div class=${cx("seg", size, block && "block", cls)} role="tablist">
    ${options.map((o) => {
      const opt = typeof o === "object" ? o : { value: o, label: o };
      return html`<button type="button" role="tab" aria-selected=${opt.value === value} disabled=${opt.disabled} title=${opt.title}
        class=${cx(opt.value === value && "on", opt.class)} onClick=${() => onChange?.(opt.value)}>${opt.label}</button>`;
    })}
  </div>`;
}

export function Field({ label, hint, error, right, children, class: cls }) {
  return html`<label class=${cx("field", cls)}>
    ${label && html`<span class="lbl">${label}${right && html`<span class="hint-right">${right}</span>`}</span>`}
    ${children}
    ${error ? html`<span class="err">${error}</span>` : hint && html`<span class="hint">${hint}</span>`}
  </label>`;
}

export function NumberInput({ value, onInput, min, max, step = "any", suffix, changed, invalid, size, placeholder, disabled }) {
  return html`<div class="input-group">
    <input class=${cx("input", size, changed && "changed", invalid && "invalid")} type="number" inputmode="decimal" value=${value ?? ""}
      min=${min} max=${max} step=${step} placeholder=${placeholder} disabled=${disabled}
      onInput=${(e) => onInput?.(e.currentTarget.value)} />
    ${suffix && html`<span class="suffix">${suffix}</span>`}
  </div>`;
}

export function Select({ value, onChange, options, size, class: cls, disabled }) {
  return html`<select class=${cx("select", size, cls)} value=${value} disabled=${disabled} onChange=${(e) => onChange?.(e.currentTarget.value)}>
    ${options.map((o) => {
      const opt = typeof o === "object" ? o : { value: o, label: o };
      return html`<option value=${opt.value} disabled=${opt.disabled}>${opt.label}</option>`;
    })}
  </select>`;
}

export function Kbd({ children }) { return html`<kbd>${children}</kbd>`; }

export function CoinIcon({ symbol, size = "" }) {
  const m = coinMeta(symbol);
  return html`<span class=${cx("coin", size)} style=${{ background: m.color }} title=${m.name}>${m.glyph}</span>`;
}

/** Live price that flashes green/red as it ticks. */
export function Price({ value, class: cls }) {
  const prev = useRef(value);
  const [flash, setFlash] = useState("");
  const [n, setN] = useState(0);
  useEffect(() => {
    if (isNum(prev.current) && isNum(value) && Number(value) !== Number(prev.current)) {
      setFlash(Number(value) > Number(prev.current) ? "flash-up" : "flash-down");
      setN((x) => x + 1);
    }
    prev.current = value;
  }, [value]);
  return html`<span key=${n} class=${cx("num", flash, cls)}>${fmtPrice(value)}</span>`;
}

export function Pnl({ value, pct, class: cls, sign = true, d = 2 }) {
  if (!isNum(value)) return html`<span class=${cx("num faint", cls)}>—</span>`;
  return html`<span class=${cx("num", tone(value), cls)}>${fmtUsd(value, { sign, d })}${isNum(pct) ? html` <span class="faint" style="font-weight:500">(${Number(pct) > 0 ? "+" : ""}${Number(pct).toFixed(2)}%)</span>` : ""}</span>`;
}

/** Relative time that keeps itself fresh. */
export function Ago({ ts, class: cls }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 15000); return () => clearInterval(t); }, []);
  return html`<span class=${cls} title=${ts ? new Date(typeof ts === "number" ? (ts > 1e12 ? ts : ts * 1000) : ts).toLocaleString() : ""}>${timeAgo(ts)}</span>`;
}

export function Spark({ values, tone: t, fill, height = 46, stroke = 1.8 }) {
  const vals = (values || []).map(Number).filter(Number.isFinite);
  const id = useMemo(() => "sp" + Math.random().toString(36).slice(2, 8), []);
  if (vals.length < 2) return html`<svg width="100%" height=${height}></svg>`;
  const w = 200, h = height, lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pts = vals.map((v, i) => [(i / (vals.length - 1)) * w, h - 4 - ((v - lo) / span) * (h - 8)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const col = t === "down" ? "var(--down)" : t === "up" ? "var(--up)" : t === "flat" ? "var(--text-3)" : "var(--accent)";
  return html`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height=${h} aria-hidden="true">
    ${fill && html`<defs><linearGradient id=${id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color=${col} stop-opacity=".28" /><stop offset="1" stop-color=${col} stop-opacity="0" /></linearGradient></defs>
      <path d=${d + ` L${w} ${h} L0 ${h} Z`} fill=${"url(#" + id + ")"} />`}
    <path d=${d} fill="none" stroke=${col} stroke-width=${stroke} vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round" />
  </svg>`;
}

export function Meter({ value, max = 100, tone: t, title }) {
  const pct = max > 0 && isNum(value) ? clamp((Number(value) / max) * 100, 0, 100) : 0;
  const auto = t || (pct >= 90 ? "down" : pct >= 70 ? "warn" : "up");
  return html`<div class=${cx("meter", auto)} title=${title}><span style=${{ width: pct + "%" }}></span></div>`;
}

export function Gauge({ value, max = 100, size = 120, label, display }) {
  const pct = max > 0 && isNum(value) ? clamp(Number(value) / max, 0, 1) : 0;
  const r = 46, c = Math.PI * r;
  const col = pct >= 0.9 ? "var(--down)" : pct >= 0.7 ? "var(--warn)" : "var(--up)";
  return html`<div class="gauge-card">
    <svg width=${size} height=${size * 0.62} viewBox="0 0 120 70" aria-hidden="true">
      <path d="M14 64 A46 46 0 0 1 106 64" fill="none" stroke="var(--surface-3)" stroke-width="10" stroke-linecap="round" />
      <path d="M14 64 A46 46 0 0 1 106 64" fill="none" stroke=${col} stroke-width="10" stroke-linecap="round"
        stroke-dasharray=${`${(pct * c).toFixed(1)} ${c.toFixed(1)}`} style="transition:stroke-dasharray .6s var(--ease)" />
    </svg>
    <div class="g-val num">${display ?? Math.round(pct * 100) + "%"}</div>
    <div class="muted" style="font-size:12.5px">${label}</div>
  </div>`;
}

export function Empty({ icon = "info", title, body, action }) {
  return html`<div class="empty"><div class="e-icon"><${Icon} name=${icon} size=${20} /></div>
    ${title && html`<strong>${title}</strong>`}${body && html`<span>${body}</span>`}${action}</div>`;
}

export function Skeleton({ w = "100%", h = 14, r }) {
  return html`<div class="skeleton" style=${{ width: w, height: h + "px", borderRadius: r }}></div>`;
}

export function Callout({ tone: t = "", icon, title, children, action }) {
  const ic = icon || (t === "down" ? "alert" : t === "warn" ? "alert" : t === "up" ? "check" : "info");
  return html`<div class=${cx("callout", t)}>
    <${Icon} name=${ic} size=${17} class=${cx("c-icon", t === "warn" ? "warn" : t === "down" ? "down" : t === "up" ? "up" : "accent")} />
    <div class="grow">${title && html`<strong>${title}</strong>`}<div class="muted">${children}</div></div>${action}</div>`;
}

export function Tabs({ tabs, value, onChange }) {
  return html`<div class="tabs" role="tablist">${tabs.map((t) => {
    const tab = typeof t === "object" ? t : { value: t, label: t };
    return html`<button type="button" role="tab" class=${tab.value === value && "on"} onClick=${() => onChange(tab.value)}>${tab.label}${isNum(tab.count) ? html` <span class="faint">${tab.count}</span>` : ""}</button>`;
  })}</div>`;
}

/** Click-outside / Esc aware popover anchored under its trigger. */
export function Popover({ trigger, children, align = "right", width }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const close = () => setOpen(false);
  return html`<div class="anchor" ref=${ref}>
    ${trigger({ open, toggle: () => setOpen((o) => !o) })}
    ${open && html`<div class="popover" style=${{ [align]: 0, top: "calc(100% + 8px)", width }}>${typeof children === "function" ? children(close) : children}</div>`}
  </div>`;
}

// ── modal frames ──
export function ModalFrame({ title, sub, children, foot, close, icon, iconTone }) {
  return html`<div class="modal-head">
      ${icon && html`<div class=${cx("engine-orb", iconTone || "paused")} style="width:38px;height:38px;border-radius:12px"><${Icon} name=${icon} size=${18} /></div>`}
      <div class="grow"><h3>${title}</h3>${sub && html`<p>${sub}</p>`}</div>
      <${Button} variant="ghost" size="sm" icon="x" onClick=${close} aria-label="Close" />
    </div>
    <div class="modal-body">${children}</div>
    ${foot && html`<div class="modal-foot">${foot}</div>`}`;
}

export function DrawerFrame({ title, sub, children, foot, close }) {
  return html`<div class="drawer-head"><div class="grow"><h3>${title}</h3>${sub && html`<div class="muted" style="font-size:12.5px;margin-top:2px">${sub}</div>`}</div>
      <${Button} variant="ghost" size="sm" icon="x" onClick=${close} aria-label="Close" /></div>
    <div class="drawer-body">${children}</div>
    ${foot && html`<div class="drawer-foot">${foot}</div>`}`;
}

function ConfirmBody({ opts, resolve, close }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const need = opts.typeToConfirm;
  const ok = !need || typed.trim().toUpperCase() === need.toUpperCase();
  const done = (v) => { resolve(v); close(); };
  return html`<${ModalFrame} title=${opts.title} sub=${opts.sub} close=${() => done(false)} icon=${opts.icon || (opts.tone === "danger" ? "alert" : "info")}
      iconTone=${opts.tone === "danger" ? "off" : opts.tone === "success" ? "on" : "paused"}
      foot=${html`<${Button} variant="ghost" onClick=${() => done(false)}>Cancel</${Button}>
        <${Button} variant=${opts.tone === "danger" ? "danger solid" : opts.tone === "success" ? "success solid" : "primary"} disabled=${!ok} loading=${busy}
          onClick=${async () => { setBusy(true); done(true); }}>${opts.confirm || "Confirm"}</${Button}>`}>
    <div class="stack-sm">
      ${opts.body && html`<div class="muted" style="font-size:13.5px">${opts.body}</div>`}
      ${need && html`<${Field} label=${html`Type <b class="mono">${need}</b> to confirm`}>
        <input class="input" autofocus value=${typed} onInput=${(e) => setTyped(e.currentTarget.value)} onKeyDown=${(e) => { if (e.key === "Enter" && ok) done(true); }} /></${Field}>`}
    </div>
  </${ModalFrame}>`;
}

/** Promise<boolean> confirmation dialog. */
export function confirmDialog(opts) {
  return new Promise((resolve) => {
    let settled = false;
    const res = (v) => { if (!settled) { settled = true; resolve(v); } };
    openModal((close) => html`<${ConfirmBody} opts=${opts} resolve=${res} close=${() => { res(false); close(); }} />`, { size: "sm" });
  });
}

export function Section({ title, sub, actions, children }) {
  return html`<div class="stack-sm">
    <div class="row between"><div><h3 style="font-size:15px">${title}</h3>${sub && html`<div class="muted" style="font-size:13px;margin-top:2px">${sub}</div>`}</div>${actions}</div>
    ${children}
  </div>`;
}

export function Money({ value, d = 2, class: cls }) {
  return html`<span class=${cx("num", cls)}>${fmtUsd(value, { d })}</span>`;
}

export function SideChip({ side, size, entry }) {
  if (!side) return null;
  return html`<span class=${cx("pos-chip", side)}><${Icon} name=${side === "long" ? "arrowUpRight" : "arrowDownRight"} size=${13} stroke=${2.4} />${side.toUpperCase()}${size ? " " + size : ""}${entry ? html` <span style="opacity:.75;font-weight:550">@ ${fmtPrice(entry)}</span>` : ""}</span>`;
}
