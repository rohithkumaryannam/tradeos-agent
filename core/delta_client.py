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
import os
import time
from typing import Any
from urllib.parse import urlencode

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
    "fills": "/v2/fills",
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
        # Route through a fixed-IP outbound proxy, so Delta sees one stable IP
        # to whitelist regardless of this machine's own (changing) public IP.
        proxy_url = os.getenv("DELTA_PROXY_URL", "").strip()
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}

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
            query = "?" + urlencode(params)
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
            # A hosted proxy can expire or reject traffic independently of
            # Delta. Direct fallback is opt-in only: live accounts must keep
            # using the whitelisted fixed-IP proxy by default.
            if self.session.proxies and os.getenv("DELTA_DIRECT_FALLBACK", "false").strip().lower() in ("1", "true", "yes"):
                try:
                    direct = requests.Session()
                    resp = direct.request(
                        method, url, headers=headers,
                        data=body_str if body_str else None, timeout=15,
                    )
                except requests.RequestException as direct_error:
                    raise DeltaError(f"network error: {direct_error}") from direct_error
            else:
                raise DeltaError(f"network error: {e}") from e

        if resp.status_code >= 400:
            raise DeltaError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise DeltaError(f"{method} {path}: invalid JSON response") from e
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

    def get_orders(self) -> list[dict]:
        result = self._request("GET", ENDPOINTS["orders"], signed=True)
        return result if isinstance(result, list) else []

    def get_fills(self, product_ids: int | str | None = None, page_size: int = 50) -> list[dict]:
        params: dict[str, Any] = {"page_size": min(50, max(1, int(page_size)))}
        if product_ids is not None:
            params["product_ids"] = product_ids
        result = self._request("GET", ENDPOINTS["fills"], params=params, signed=True)
        return result if isinstance(result, list) else []

    def place_order(
        self,
        product_id: int,
        size: int,
        side: str,                 # "buy" | "sell"
        order_type: str = "market_order",
        limit_price: float | None = None,
        reduce_only: bool = False,
        bracket_stop_loss_price: float | None = None,
        bracket_take_profit_price: float | None = None,
        bracket_stop_trigger_method: str = "last_traded_price",
        client_order_id: str | None = None,
    ) -> dict:
        """bracket_stop_loss_price / bracket_take_profit_price: when both are
        given on an entry order, Delta attaches a real exchange-side bracket
        (resting stop-loss + take-profit) to the resulting position — per
        docs.delta.exchange's CreateOrderRequest. This means protection
        survives even if our own process is down, unlike a bot-side-only
        watch. trigger method "last_traded_price" matches the candle-close
        price our own local SL/TP math uses, so the two stay consistent.
        """
        body: dict = {
            "product_id": product_id,
            "size": size,
            "side": side,
            "order_type": order_type,
        }
        if client_order_id:
            body["client_order_id"] = client_order_id
        if order_type == "limit_order" and limit_price is not None:
            body["limit_price"] = str(limit_price)
        if reduce_only:
            body["reduce_only"] = True
        if bracket_stop_loss_price is not None and bracket_take_profit_price is not None:
            body["bracket_stop_loss_price"] = str(bracket_stop_loss_price)
            body["bracket_take_profit_price"] = str(bracket_take_profit_price)
            body["bracket_stop_trigger_method"] = bracket_stop_trigger_method
        return self._request("POST", ENDPOINTS["orders"], body=body, signed=True)

    def cancel_order(self, order_id: int, product_id: int) -> dict:
        body = {"id": order_id, "product_id": product_id}
        return self._request("DELETE", ENDPOINTS["orders"], body=body, signed=True)

    def has_bracket(self, product_id: int) -> bool:
        """True if a stop_loss_order AND take_profit_order are both resting
        (state=pending — NOT "open"; a bracket's conditional legs use their
        own pending state, confirmed by live testing) for this product. More
        trustworthy than reading bracket_* fields off the entry order's own
        response, which can echo the request back without confirming the
        bracket was actually created server-side."""
        sl, tp = self.get_bracket_prices(product_id)
        return sl is not None and tp is not None

    def cancel_bracket_orders(self, product_id: int) -> int:
        """Cancel any resting stop_loss_order/take_profit_order for this
        product — used when the bot closes a position itself (e.g. a
        stop-and-reverse exit) before the exchange bracket fires, so the old
        bracket doesn't linger as a stray reduce-only order. Returns how many
        were cancelled."""
        orders = self._request("GET", ENDPOINTS["orders"], params={"product_ids": product_id}, signed=True)
        cancelled = 0
        for o in (orders or []):
            if o.get("state") == "pending" and o.get("stop_order_type") in (
                "stop_loss_order", "take_profit_order"
            ):
                self.cancel_order(o["id"], product_id)
                cancelled += 1
        return cancelled

    def get_bracket_prices(self, product_id: int) -> tuple[float | None, float | None]:
        """(stop_loss_price, take_profit_price) of the resting bracket legs
        for this product, or (None, None) if no bracket is attached. Used both
        to confirm a just-placed bracket and to recover SL/TP on reconnect —
        e.g. after a server restart — for a position opened before the
        restart."""
        orders = self._request("GET", ENDPOINTS["orders"], params={"product_ids": product_id}, signed=True)
        sl = tp = None
        for o in (orders or []):
            if o.get("state") != "pending":
                continue
            if o.get("stop_order_type") == "stop_loss_order":
                sl = float(o["stop_price"])
            elif o.get("stop_order_type") == "take_profit_order":
                tp = float(o["stop_price"])
        return sl, tp
