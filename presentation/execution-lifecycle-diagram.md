# Execution Lifecycle Diagram - From Market Tick to Confirmed Position

## Where to use it

Use this on the **How the solution works** or **Strategy execution** slide. It makes the difference between a strategy signal and an actual broker order clear.

## Diagram goal

Show that the platform does not blindly trade on every tick. It waits for valid market data, builds closed candles where needed, evaluates a rule, applies risk/reconciliation checks, and uses confirmed broker trades to update P&L and position state.

## Mermaid source

```mermaid
sequenceDiagram
    participant M as 021 market WebSocket
    participant W as AlgoRhythm worker
    participant C as Candle + strategy engine
    participant R as Risk + reconciliation gate
    participant B as 021 order API / order socket
    participant D as PostgreSQL ledger

    M->>W: Binary market snapshot
    W->>D: Store fresh quote and update mark price
    W->>C: Build/close 1-minute and 5-minute candles
    C->>C: Evaluate time, breakout, or candle-momentum rule

    alt No valid signal or strategy is already in a position
        C-->>W: No entry
    else Signal is produced
        C->>R: Buy/sell/close intention
        R->>D: Check strategy state, limits, open orders,<br/>fresh quote, session and reconciliation
        alt Blocked
            R->>D: Persist risk event / block reason
        else Approved
            R->>D: Persist SUBMITTING order + reserve quantity
            R->>B: Submit 021 market order
            B-->>W: Order acknowledgement / lifecycle signal
            W->>B: Reconcile orders, trades and positions via REST
            B-->>W: Confirmed broker trades
            W->>D: Write deduplicated execution, position, P&L and charges
            D-->>W: Updated strategy ledger
        end
    end
```

## Speaker notes

- “A signal is only an intention. Risk admission is a separate, mandatory step.”
- “The worker records the order before the remote call so a crash cannot silently lose ownership.”
- “Only confirmed broker trade rows create executions. WebSocket events trigger reconciliation but are not treated as duplicate fill data.”
- “For the recurring candle-momentum strategy, a fresh closed candle is required before another decision can be made.”

## Design recommendation

Use a sequence diagram rather than a flowchart because timing and confirmation matter. Highlight the `SUBMITTING` persistence step and the confirmed-trade step with callouts in the final slide design.
