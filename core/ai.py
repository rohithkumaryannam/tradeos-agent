"""Small, fail-closed AI client for analysis and narration.

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
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl:free"


def _key() -> str:
    # .strip(): hosting dashboards commonly leave a trailing newline when a
    # value is pasted into a multi-line env var box.
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def _openrouter_key() -> str:
    return (os.getenv("OPENROUTER_API_KEY") or "").strip()


def available() -> bool:
    return bool(_openrouter_key() or _key())


def model_name() -> str:
    if _openrouter_key():
        return os.getenv("OPENROUTER_MODEL", _OPENROUTER_MODEL).strip()
    return os.getenv("GEMINI_MODEL", _DEFAULT_MODEL).strip()


def ask_json(prompt: str, *, system: str = "", max_tokens: int = 400,
             temperature: float = 0.2, timeout: int = 20) -> dict | None:
    """Like ask(), but instructs the model to reply with a single JSON object
    and parses it. Returns None on any failure (unavailable, bad JSON, etc.)
    — callers must treat that as "could not understand," never as an action.
    Strips ```json code fences, and tolerates free models (e.g. the
    OpenRouter free tier) that wrap the object in prose despite instructions,
    by extracting the outermost {...} span before parsing."""
    import json as _json
    json_system = (system + "\n\n" if system else "") + (
        "Respond with ONLY a single valid JSON object. No prose before or "
        "after it, no markdown code fences."
    )
    text = ask(prompt, system=json_system, max_tokens=max_tokens,
               temperature=temperature, timeout=timeout, json_mode=True)
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = _json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = _json.loads(text[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            pass
    log.warning("ask_json: model did not return valid JSON: %r", text[:200])
    return None


def ask(prompt: str, *, system: str = "", max_tokens: int = 256,
        temperature: float = 0.4, timeout: int = 20,
        thinking_budget: int | None = None, json_mode: bool = False) -> str | None:
    """Single-turn text generation. Returns the model's text, or None on any
    failure (never raises).

    thinking_budget: for Gemini thinking models, tokens reserved for internal
    reasoning. 0 disables thinking (fast, and leaves all of max_tokens for the
    visible answer) — use it when you want a short, direct output. Left None, the
    model decides, so give max_tokens enough headroom for reasoning + answer."""
    openrouter_key = _openrouter_key()
    key = _key()
    if not openrouter_key and not key:
        return None
    if openrouter_key:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {"model": model_name(), "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {openrouter_key}",
                   "Content-Type": "application/json",
                   "HTTP-Referer": "https://tradeos-agent-production.up.railway.app",
                   "X-Title": "TradeOS"}
        try:
            r = requests.post(_OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code >= 400 and json_mode:
                # Some free-tier models reject response_format outright — retry plain.
                log.warning("OpenRouter %s -> %s with response_format, retrying without",
                            model_name(), r.status_code)
                payload = dict(payload)
                del payload["response_format"]
                r = requests.post(_OPENROUTER_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code >= 400:
                log.warning("OpenRouter %s -> %s", model_name(), r.status_code)
                return None
            choices = (r.json().get("choices") or [{}])
            message = choices[0].get("message") or {}
            text = message.get("content") or message.get("reasoning") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
            return str(text).strip() or None
        except Exception as e:
            log.warning("OpenRouter call failed: %s", e)
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
