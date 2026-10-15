"""Minimal Google Gemini client.

Fail-closed by design: any missing key, network error, HTTP error or unexpected
payload returns None (and logs a warning) so the trading bot never breaks when
the AI is unavailable. The API key is read from GEMINI_API_KEY in .env; the
model from GEMINI_MODEL (default below).

Get a key at https://aistudio.google.com/apikey — it should start with "AIza".
"""
from __future__ import annotations
import logging
import os

import requests

log = logging.getLogger("tradeos.ai")

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_DEFAULT_MODEL = "gemini-3.8-flash"


def _key() -> str:
    # .strip(): hosting dashboards commonly leave a trailing newline when a
    # value is pasted into a multi-line env var box.
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def available() -> bool:
    return bool(_key())


def model_name() -> str:
    return os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()


def ask(prompt: str, *, system: str = "", max_tokens: int = 256,
        temperature: float = 0.4, timeout: int = 20,
        thinking_budget: int | None = None) -> str | None:
    """Single-turn text generation. Returns the model's text, or None on any
    failure (never raises).

    thinking_budget: for Gemini thinking models, tokens reserved for internal
    reasoning. 0 disables thinking (fast, and leaves all of max_tokens for the
    visible answer) — use it when you want a short, direct output. Left None, the
    model decides, so give max_tokens enough headroom for reasoning + answer."""
    key = _key()
    if not key:
        return None
    gen: dict = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if thinking_budget is not None:
        gen["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        r = requests.post(_URL.format(model=model_name()),
                          params={"key": key}, json=body, timeout=timeout)
        if r.status_code >= 400:
            log.warning("Gemini %s -> %s: %s", model_name(), r.status_code, r.text[:200])
            return None
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            log.warning("Gemini returned no text (finishReason=%s)", cand.get("finishReason"))
            return None
        return text
    except Exception as e:
        log.warning("Gemini call failed: %s", e)
        return None
