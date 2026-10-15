"""
Delta Exchange REST client (testnet / demo).

Auth scheme (per docs.delta.exchange):
  prehash = method + timestamp + request_path + query_string + body
  signature = hex( HMAC_SHA256(api_secret, prehash) )
  headers: api-key, signature, timestamp, User-Agent, Content-Type

Only the endpoints the agent needs are implemented. Endpoint paths live in
ENDPOINTS so they're trivial to adjust if Delta changes them.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from typing import Any

import requests

# Demo / testnet. For production swap to https://api.india.delta.exchange
BASE_URL = "https://cdn-ind.testnet.deltaex.org"

ENDPOINTS = {
    "candles": "/v2/history/candles",
    "orders": "/v2/orders",
    "positions": "/v2/positions/margined",
    "balances": "/v2/wallet/balances",
    "products": "/v2/products",
    "tickers": "/v2/tickers",
}

USER_AGENT = "tradeos-agent/1.0"


class DeltaError(RuntimeError):
    pass


class DeltaClient:
    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = BASE_URL):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    # ── signing ──
    def _sign(self, method: str, path: str, query: str, body: str) -> dict[str, str]:
        ts = str(int(time.time()))
        prehash = method + ts + path + query + body
        sig = hmac.new(
            self.api_secret.encode(), prehash.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "api-key": self.api_key,
            "signature": sig,
            "timestamp": ts,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        signed: bool = False,
    ) -> Any:
        params = params or {}
        # Build query string with leading '?' (signed into the prehash).
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        body_str = json.dumps(body, separators=(",", ":")) if body else ""

        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        if signed:
            headers = self._sign(method, path, query, body_str)

        url = self.base_url + path + query
        try:
            resp = self.session.request(
                method, url, headers=headers,
                data=body_str if body_str else None, timeout=15,
            )
        except requests.RequestException as e:
            raise DeltaError(f"network error: {e}") from e

        if resp.status_code >= 400:
            raise DeltaError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("success") is False:
            raise DeltaError(f"API error: {payload.get('error')}")
        return payload.get("result", payload) if isinstance(payload, dict) else payload

    # ── market data (public) ──
    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> list[dict]:
        """OHLC candles. start/end are unix seconds. Returns list oldest->newest."""
        params = {"symbol": symbol, "resolution": resolution, "start": start, "end": end}
        result = self._request("GET", ENDPOINTS["candles"], params=params)
        candles = result if isinstance(result, list) else []
        # Delta returns newest-first; normalize to oldest-first.
        candles = sorted(candles, key=lambda c: c.get("time", 0))
        return candles

    def get_ticker(self, symbol: str) -> dict:
        result = self._request("GET", f"{ENDPOINTS['tickers']}/{symbol}")
        return result if isinstance(result, dict) else {}

    def get_product(self, symbol: str) -> dict:
        """Product metadata (needed for product_id and contract value)."""
        result = self._request("GET", f"{ENDPOINTS['products']}/{symbol}")
        return result if isinstance(result, dict) else {}

    # ── account (signed) ──
    def get_balance(self) -> list[dict]:
        return self._request("GET", ENDPOINTS["balances"], signed=True)

    def get_positions(self) -> list[dict]:
        res = self._request("GET", ENDPOINTS["positions"], signed=True)
        return res if isinstance(res, list) else [res] if res else []

    def place_order(
        self,
        product_id: int,
        size: int,
        side: str,                 # "buy" | "sell"
        order_type: str = "market_order",
        limit_price: float | None = None,
        reduce_only: bool = False,
    ) -> dict:
        body: dict = {
            "product_id": product_id,
            "size": size,
            "side": side,
            "order_type": order_type,
        }
        if order_type == "limit_order" and limit_price is not None:
            body["limit_price"] = str(limit_price)
        if reduce_only:
            body["reduce_only"] = True
        return self._request("POST", ENDPOINTS["orders"], body=body, signed=True)

    def cancel_order(self, order_id: int, product_id: int) -> dict:
        body = {"id": order_id, "product_id": product_id}
        return self._request("DELETE", ENDPOINTS["orders"], body=body, signed=True)
