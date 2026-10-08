import uuid
from decimal import Decimal
from typing import Dict, Iterable, Optional
from .contracts import BrokerAcknowledgement, BrokerExecution, BrokerOrderRequest


class DeterministicSimulatorBroker:
    """Deterministic normalized broker used only when ENVIRONMENT=SIMULATOR."""
    def __init__(self):
        self._orders: Dict[str, BrokerAcknowledgement] = {}

    def place_order(self, request: BrokerOrderRequest, scenario: str = "full") -> BrokerAcknowledgement:
        if request.client_order_id in self._orders:
            return self._orders[request.client_order_id]
        broker_id = f"SIM-{uuid.uuid4().hex[:12].upper()}"
        ack = BrokerAcknowledgement(broker_id, "REJECTED", "Simulator rejection scenario") if scenario == "reject" else BrokerAcknowledgement(broker_id, "ACKNOWLEDGED")
        self._orders[request.client_order_id] = ack
        return ack

    def execution_plan(self, acknowledgement: BrokerAcknowledgement, quantity: int, price: Decimal, scenario: str) -> Iterable[BrokerExecution]:
        if acknowledgement.status == "REJECTED" or scenario == "pending": return []
        partial_quantity = max(1, quantity * 40 // 100)
        if scenario == "partial":
            quantities = [partial_quantity, quantity - partial_quantity]
        elif scenario == "cancel_race":
            quantities = [partial_quantity]
        else:
            quantities = [quantity]
        return [BrokerExecution(f"{acknowledgement.broker_order_id}-{i+1:02d}", acknowledgement.broker_order_id or "", qty, price + Decimal("0.20") * i) for i, qty in enumerate(quantities) if qty]

    def cancel_order(self, broker_order_id: str) -> BrokerAcknowledgement:
        return BrokerAcknowledgement(broker_order_id, "CANCELLED")

    def lookup_order(self, client_order_id: str) -> Optional[BrokerAcknowledgement]:
        return self._orders.get(client_order_id)


simulator_broker = DeterministicSimulatorBroker()
