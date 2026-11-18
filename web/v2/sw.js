// TradeOS service worker: makes the dashboard installable and shows a small
// offline page instead of the browser's error. It deliberately caches
// nothing else — a trading dashboard must never show stale data or code.
const OFFLINE = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TradeOS · offline</title><body style="margin:0;min-height:100vh;display:grid;place-content:center;gap:12px;text-align:center;
background:#08090c;color:#eceef3;font:15px system-ui,sans-serif"><div style="font-size:40px">⚡</div><b>TradeOS is offline</b>
<span style="color:#a3a9b6">Your device or the server can't be reached. Exchange-side stop-loss/take-profit orders stay active on Delta.</span>
<button onclick="location.reload()" style="justify-self:center;margin-top:8px;padding:10px 18px;border-radius:10px;border:0;background:#8b7bff;color:#fff;font-weight:600">Retry</button></body>`;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({ type: "window" }).then((wins) => {
    const win = wins.find((w) => "focus" in w);
    return win ? win.focus() : self.clients.openWindow("/");
  }));
});
