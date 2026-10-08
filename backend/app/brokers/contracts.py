from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional, Protocol


@dataclass(frozen=True)
class BrokerOrderRequest:
    client_order_id: str
    symbol: str
    side: str
    quantity: int


@dataclass(frozen=True)
class BrokerAcknowledgement:
    broker_order_id: Optional[str]
    status: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class BrokerExecution:
    execution_id: str
    broker_order_id: str
    incremental_quantity: int
    price: Decimal


class BrokerAdapter(Protocol):
    """Normalized boundary. A verified adapter must implement every operation."""
    def place_order(self, request: BrokerOrderRequest, scenario: str = "full") -> BrokerAcknowledgement: ...
    def execution_plan(self, acknowledgement: BrokerAcknowledgement, quantity: int, price: Decimal, scenario: str) -> Iterable[BrokerExecution]: ...
    def cancel_order(self, broker_order_id: str) -> BrokerAcknowledgement: ...
    def lookup_order(self, client_order_id: str) -> Optional[BrokerAcknowledgement]: ...
