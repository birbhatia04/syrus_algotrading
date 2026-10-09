# Risk and Recovery Diagram - Fail Closed, Then Reconcile

## Where to use it

Use this on the **USP / Showstopper** slide. This is the strongest technical differentiation: the product is designed to avoid creating untracked exposure when data or broker responses are uncertain.

## Diagram goal

Show the normal path, blocked-entry path, and emergency path without overloading the architecture slide.

## Mermaid source

```mermaid
flowchart TD
    A[Strategy creates an intent] --> B{Fresh quote?<br/>Inside trading session?<br/>Strategy running?}
    B -->|No| X[Block entry and show reason]
    B -->|Yes| C{Reconciled broker state?<br/>No unknown/external INTRADAY activity?}
    C -->|No| Y[Fail closed:<br/>block new entries]
    C -->|Yes| D{Lot/freeze quantity,<br/>position, daily loss and<br/>rate limits valid?}
    D -->|No| Z[Persist risk event:<br/>reject entry]
    D -->|Yes| E[Persist SUBMITTING order<br/>and reserve exposure]
    E --> F[Send order to 021]
    F --> G{Acknowledgement outcome clear?}
    G -->|Yes| H[Reconcile orders, trades<br/>and positions]
    H --> I[Update execution, strategy<br/>position, P&L and charges]
    G -->|Timeout / invalid response| J[Mark UNKNOWN and keep<br/>exposure reserved]
    J --> Y

    K[Pause, daily-loss breach,<br/>15:15 square-off or kill switch] --> L[Cancel entry remainders]
    L --> M[Reconcile racing fills]
    M --> N[Send close-only order<br/>without crossing zero]
    N --> O{Flat and reconciled?}
    O -->|Yes| P[HALTED / safe state]
    O -->|No| Q[NEEDS_ATTENTION:<br/>keep entries disabled]
```

## Speaker notes

- “When something is uncertain, AlgoRhythm chooses safety over continued trading.”
- “An API timeout is not treated as a rejection because the broker may still have accepted the order.”
- “The kill switch does not claim success until the local strategy ledgers and broker state are both flat and reconciled.”
- “Close-only orders reduce an existing attributed position; they cannot reverse or accidentally create a new position.”

## Design recommendation

Use this as a simplified high-level diagram. In the final design, colour normal flow green/neutral, blocked paths amber/red, and recovery/kill flow blue. Do not label it ‘guaranteed 10-second kill’; the implementation reports a target and escalates to `NEEDS_ATTENTION` if confirmation is unavailable.
