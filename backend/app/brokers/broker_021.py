"""021 sandbox wire protocol. Writes are never automatically retried."""
from __future__ import annotations
import csv
import gzip
import io
from urllib.parse import quote
import httpx

BASE_URL = "https://devapi.021.trade/api/developer-api/v1"
WS_BASE = "wss://devapi.021.trade/api/developer/websocket"


class BrokerError(Exception):
    def __init__(self, message: str, *, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


class Broker021Adapter:
    def __init__(self, access_token: str, *, transport=None):
        self.client = httpx.AsyncClient(
            base_url=BASE_URL, headers={"Authorization": f"Bearer {access_token}"},
            # The sandbox occasionally takes more than 1.5 seconds to build the
            # account-wide order/trade snapshots used for reconciliation.
            timeout=httpx.Timeout(6.0, connect=5.0), transport=transport,
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, body=None):
        try:
            response = await self.client.request(method, path, json=body)
        except httpx.HTTPError as error:
            raise BrokerError("021 request failed or timed out", ambiguous=method != "GET") from error
        if response.status_code == 401:
            raise BrokerError("021 token expired or revoked; obtain one new shared token and restart services")
        if response.status_code >= 500:
            raise BrokerError(f"021 HTTP {response.status_code}; reconcile before retrying a write", ambiguous=method != "GET")
        if response.status_code == 429:
            raise BrokerError("021 rate limit reached; entries paused until reconciliation succeeds")
        try:
            payload = response.json()
        except ValueError as error:
            raise BrokerError("Invalid 021 response", ambiguous=method != "GET") from error
        if response.is_error or (isinstance(payload, dict) and payload.get("success") is False):
            message = payload.get("error") if isinstance(payload, dict) else None
            raise BrokerError(str(message or f"021 HTTP {response.status_code}")[:500])
        return payload.get("data") if isinstance(payload, dict) and "success" in payload else payload

    async def _list(self, path):
        result = await self.request("GET", path)
        if not isinstance(result, list):
            raise BrokerError("Expected a 021 array response")
        return result

    async def list_orders(self):
        return await self._list("/orders")

    async def order_trades(self, order_id):
        # Global /trades does not document an orderId. Scope trades for attribution.
        return await self._list(f"/orders/{quote(str(order_id), safe='')}/trades")

    async def list_trades(self):
        return await self._list("/trades")

    async def positions(self):
        return await self._list("/portfolio/positions")

    async def place_order(self, token, signed_quantity, price_paise=0):
        data = await self.request("POST", "/orders", {
            "exchange": "NSE", "token": token, "qty": signed_quantity,
            "price": price_paise, "book": "RL", "product": "INTRADAY", "validity": "Day",
        })
        if not isinstance(data, dict) or not data.get("orderId"):
            raise BrokerError("021 accepted request without a usable order ID", ambiguous=True)
        return str(data["orderId"])

    async def cancel_order(self, order_id, token):
        return await self.request("DELETE", f"/orders/{quote(str(order_id), safe='')}", {
            "exchange": "NSE", "token": token, "product": "INTRADAY",
        })

    async def websocket_url(self, channel):
        if channel not in {"market", "orders"}:
            raise ValueError("Invalid websocket channel")
        data = await self.request("GET", "/websocket/ephemeral-key")
        if not isinstance(data, dict) or not data.get("token"):
            raise BrokerError("Missing websocket ephemeral key")
        return f"{WS_BASE}/{channel}?token={quote(data['token'], safe='')}"

    async def instruments(self):
        try:
            response = await self.client.get("/instruments", timeout=20)
            response.raise_for_status()
            data = response.content
            if data.startswith(b"\x1f\x8b"):
                data = gzip.decompress(data)
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
            if not rows or not {"token", "exchange", "symbol", "ticksize"}.issubset(rows[0]):
                raise ValueError("Unexpected instrument CSV")
            return rows
        except (httpx.HTTPError, ValueError, OSError, UnicodeError) as error:
            raise BrokerError("Unable to download 021 instrument master; check token and retry") from error
