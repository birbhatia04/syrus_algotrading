from .contracts import BrokerAcknowledgement, BrokerOrderRequest


class Broker021Adapter:
    """Deliberately disabled until official 021 documentation and credentials exist."""
    def place_order(self, request: BrokerOrderRequest, scenario: str = "full") -> BrokerAcknowledgement:
        raise RuntimeError("021 adapter is not configured: verified API mappings are required")
