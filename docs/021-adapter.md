# 021 sandbox adapter boundary

The application does **not** contain invented 021 endpoints. `BrokerAdapter` defines the normalized operations the platform needs: place, cancel, lookup, executions, positions, and eventually market subscription. The checked-in `Broker021Adapter` fails closed.

Before enabling it, map from official 021 documentation and test credentials:

- authentication and token lifecycle;
- order request/response fields and broker status mapping;
- a documented idempotency or client-order lookup mechanism;
- whether execution quantities are incremental or cumulative;
- order/event streaming, sequence and reconnect behavior;
- cancellation/fill race semantics;
- instrument identifiers, tick timestamps, price precision and quantity constraints;
- positions, trades and charge data used for reconciliation.

Until each item is verified and covered by contract tests, `ENVIRONMENT=SIMULATOR` is the only supported mode.
