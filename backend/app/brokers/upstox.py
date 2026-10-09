"""Upstox Sandbox order adapter. It is never configured for live-money orders."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .contracts import BrokerAcknowledgement, BrokerOrderRequest
from ..config import settings


SANDBOX_URL = "https://api-sandbox.upstox.com/v3"


class UpstoxSandboxBroker:
    def _headers(self) -> dict[str, str]:
        if not settings.upstox_sandbox_access_token:
            raise RuntimeError("Upstox Sandbox access token is not configured")
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.upstox_sandbox_access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(SANDBOX_URL + path, data=body, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw.decode()) if raw else {}
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read().decode()).get("errors", [{}])[0].get("message")
            except (ValueError, AttributeError, IndexError):
                detail = None
            raise RuntimeError(f"Upstox Sandbox HTTP {error.code}: {detail or 'request rejected'}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Upstox Sandbox connection failed: {error.reason}") from error

    def place_order(self, request: BrokerOrderRequest) -> BrokerAcknowledgement:
        if request.symbol != settings.upstox_default_symbol:
            return BrokerAcknowledgement(None, "REJECTED", f"No Upstox instrument mapping for {request.symbol}")
        try:
            response = self._request("POST", "/order/place", {
                "quantity": request.quantity,
                "product": "D",
                "validity": "DAY",
                "price": 0,
                "tag": request.client_order_id[-40:],
                "instrument_token": settings.upstox_default_instrument_key,
                "order_type": "MARKET",
                "transaction_type": request.side,
                "disclosed_quantity": 0,
                "trigger_price": 0,
                "is_amo": False,
                "slice": False,
            })
            order_id = response.get("data", {}).get("order_id")
            return BrokerAcknowledgement(order_id, "ACKNOWLEDGED" if order_id else "REJECTED", None if order_id else "Upstox Sandbox did not return an order ID")
        except RuntimeError as error:
            return BrokerAcknowledgement(None, "REJECTED", str(error))

    def cancel_order(self, broker_order_id: str) -> BrokerAcknowledgement:
        try:
            self._request("DELETE", f"/order/cancel?{urllib.parse.urlencode({'order_id': broker_order_id})}")
            return BrokerAcknowledgement(broker_order_id, "CANCELLED")
        except RuntimeError as error:
            return BrokerAcknowledgement(broker_order_id, "UNKNOWN", str(error))


upstox_sandbox_broker = UpstoxSandboxBroker()
