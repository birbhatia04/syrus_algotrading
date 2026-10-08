"""Small, dependency-free adapter for Alpaca's paper Trading API.

Credentials are loaded only by the backend settings object.  This module never
logs request headers or secrets.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..config import settings
from .contracts import BrokerAcknowledgement, BrokerOrderRequest


PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
STATUS_MAP = {
    "new": "ACKNOWLEDGED", "accepted": "ACKNOWLEDGED", "pending_new": "ACKNOWLEDGED",
    "accepted_for_bidding": "ACKNOWLEDGED", "pending_replace": "ACKNOWLEDGED",
    "partially_filled": "PARTIALLY_FILLED", "filled": "FILLED", "canceled": "CANCELLED",
    "expired": "CANCELLED", "replaced": "CANCELLED", "rejected": "REJECTED",
    "suspended": "UNKNOWN", "pending_cancel": "ACKNOWLEDGED", "pending_review": "ACKNOWLEDGED",
}


@dataclass(frozen=True)
class AlpacaOrderSnapshot:
    broker_order_id: str
    status: str
    filled_qty: int
    average_fill_price: Decimal | None
    reason: str | None = None


@dataclass(frozen=True)
class AlpacaFill:
    execution_id: str
    quantity: int
    price: Decimal


class AlpacaPaperBroker:
    def _headers(self) -> dict[str, str]:
        if not settings.alpaca_configured:
            raise RuntimeError("Alpaca Paper credentials are not configured")
        return {
            "APCA-API-KEY-ID": settings.alpaca_api_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret_key,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, data_api: bool = False) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request((DATA_URL if data_api else PAPER_URL) + path, data=body, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw.decode()) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                detail = json.loads(raw.decode()).get("message", "Alpaca rejected the request")
            except (ValueError, AttributeError):
                detail = "Alpaca rejected the request"
            raise RuntimeError(f"Alpaca HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Alpaca connection failed: {error.reason}") from error

    def place_order(self, request: BrokerOrderRequest) -> BrokerAcknowledgement:
        # Alpaca client IDs are limited; preserve a stable unique suffix from our durable ID.
        client_order_id = f"ar-{request.client_order_id[-45:]}"
        try:
            row = self._request("POST", "/v2/orders", {
                "symbol": request.symbol,
                "qty": str(request.quantity),
                "side": request.side.lower(),
                "type": "market",
                "time_in_force": "day",
                "client_order_id": client_order_id,
            })
        except RuntimeError as error:
            return BrokerAcknowledgement(None, "REJECTED", str(error))
        return BrokerAcknowledgement(row.get("id"), STATUS_MAP.get(row.get("status", ""), "UNKNOWN"), row.get("reject_reason"))

    def cancel_order(self, broker_order_id: str) -> BrokerAcknowledgement:
        try:
            self._request("DELETE", f"/v2/orders/{broker_order_id}")
            return BrokerAcknowledgement(broker_order_id, "CANCELLED")
        except RuntimeError as error:
            return BrokerAcknowledgement(broker_order_id, "UNKNOWN", str(error))

    def order(self, broker_order_id: str) -> AlpacaOrderSnapshot:
        row = self._request("GET", f"/v2/orders/{broker_order_id}")
        average = row.get("filled_avg_price")
        return AlpacaOrderSnapshot(
            broker_order_id=broker_order_id,
            status=STATUS_MAP.get(row.get("status", ""), "UNKNOWN"),
            filled_qty=int(Decimal(row.get("filled_qty") or "0")),
            average_fill_price=Decimal(average) if average else None,
            reason=row.get("reject_reason"),
        )

    def fills(self, broker_order_id: str) -> list[AlpacaFill]:
        # Activities carry stable fill IDs and prices, allowing idempotent replay.
        rows = self._request("GET", "/v2/account/activities/FILL?direction=asc&page_size=100")
        return [
            AlpacaFill(str(row["id"]), int(Decimal(row["qty"])), Decimal(row["price"]))
            for row in rows if row.get("order_id") == broker_order_id
        ]


alpaca_paper_broker = AlpacaPaperBroker()
