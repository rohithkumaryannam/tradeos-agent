"""Optional out-of-band alerts, disabled unless environment variables are set."""
from __future__ import annotations
import json, os, smtplib, threading, time, urllib.request
from email.message import EmailMessage

_last, _lock = {}, threading.Lock()

def configured():
    return bool(os.getenv("TRADEOS_ALERT_WEBHOOK_URL", "").strip()
                or (os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())
                or (os.getenv("SMTP_HOST", "").strip() and os.getenv("ALERT_EMAIL_TO", "").strip()))

def notify(event: str, detail: str, cooldown: int = 60):
    if not configured(): return
    key, now = f"{event}:{detail}", time.time()
    with _lock:
        if now - _last.get(key, 0) < cooldown: return
        _last[key] = now
    threading.Thread(target=_send, args=(event, detail), daemon=True).start()

def _post(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5): pass

def _send(event, detail):
    text = f"TradeOS {event.upper()}: {detail}"
    try:
        webhook = os.getenv("TRADEOS_ALERT_WEBHOOK_URL", "").strip()
        if webhook: _post(webhook, {"text": text, "content": text})
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if token and chat: _post(f"https://api.telegram.org/bot{token}/sendMessage", {"chat_id": chat, "text": text})
        host, recipient = os.getenv("SMTP_HOST", "").strip(), os.getenv("ALERT_EMAIL_TO", "").strip()
        if host and recipient:
            msg = EmailMessage(); msg["Subject"] = f"TradeOS {event}"; msg["From"] = os.getenv("ALERT_EMAIL_FROM", recipient); msg["To"] = recipient; msg.set_content(text)
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=5) as smtp:
                if os.getenv("SMTP_TLS", "1") != "0": smtp.starttls()
                user = os.getenv("SMTP_USER", "").strip()
                if user: smtp.login(user, os.getenv("SMTP_PASSWORD", ""))
                smtp.send_message(msg)
    except Exception:
        return
