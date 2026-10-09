# AlgoRhythm - Presentation Brief

## Suggested deck flow

1. Problem understanding
2. Problem solution
3. Architecture and product workflow
4. Three strategies
5. USP: safety and recovery
6. Implementation plan
7. Business plan

Use [Architecture diagram](architecture-diagram.md) on slide 3, [Execution lifecycle diagram](execution-lifecycle-diagram.md) on slide 4 or 5, and [Risk and recovery diagram](risk-recovery-diagram.md) on slide 5.

## Problem Understanding

- Algorithmic trading needs more than buy/sell signals.
- A complete system must handle:
  - live market data
  - broker order placement
  - partial fills
  - positions and P&L
  - broker reconciliation
  - risk limits and emergency exits
- The 021-03 challenge requires:
  - 021 WebSocket market data
  - 021 order API integration
  - two mandatory strategies
  - one original strategy
- India-specific constraints:
  - prices transmitted in paise
  - signed quantity represents buy/sell
  - lot and freeze limits
  - IST market session
  - intraday square-off

## Problem Solution

- **AlgoRhythm:** web-based intraday strategy-execution platform.
- **Market:** NSE cash instruments through 021 Sandbox.
- **Order type:** `INTRADAY` market orders.
- **Frontend:** strategy controls, profile, orders, positions, risk, kill switch.
- **Backend:** persistent users, orders, executions, positions, P&L, risk events, broker state.
- **Worker:** receives market data, creates candles, evaluates strategies, checks risk, sends/reconciles orders.
- **Environment:** sandbox only; no real-money claim.

## Implemented Strategies

### 1. Required: 09:15 entry / 15:15 exit

- One entry attempt on first fresh quote from **09:15:00-09:15:59 IST**.
- Default side: BUY.
- Configurable: BUY/SELL and quantity while paused and flat.
- No late catch-up after 09:16.
- At 15:15:
  - cancel outstanding entry remainder
  - reconcile actual fills
  - close only the strategy-owned quantity

### 2. Required: 1% opening breakout

- Read official day-open from full 021 market-data packet.
- BUY when `LTP >= Open x 1.01`.
- SELL when `LTP <= Open x 0.99`.
- Maximum one entry attempt per day.
- After confirmed fill:
  - 5% target
  - 5% stop-loss
  - levels based on weighted average fill price
  - tick-size rounding
- Close on target, stop-loss, or 15:15 square-off.

### 3. Original: Recurring candle momentum

- Also labelled **Dual-timeframe candle pulse**.
- Build closed 1-minute and 5-minute candles from the live feed.
- Calculate percentage body of:
  - latest closed 1-minute candle
  - latest 5-minute candle
- Combined movement:
  - non-negative -> BUY
  - negative -> SELL
- Flat-only entry; no pyramiding.
- Exit on:
  - opposite 1-minute candle
  - configured holding period after latest fill
  - 15:15 IST
- Re-enter only after the previous exit is confirmed.
- Cap: six entry cycles per day.

**Accuracy note:** README says 1-minute default hold; latest code configuration uses 3 minutes. Present it as a configurable holding period until both are aligned.

## Tech Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite, Lucide | Dashboard and controls |
| API | Python, FastAPI, Pydantic, Uvicorn | APIs, validation, user operations |
| Execution | asyncio, `websockets`, `httpx` | Streaming, broker calls, reconciliation |
| Database | PostgreSQL, SQLAlchemy, Alembic | Durable trading ledger and migrations |
| Deployment | Docker Compose, Nginx | Repeatable local environment |
| Broker | 021 Developer Sandbox | Market data and simulated orders |
| Testing | Pytest, Vitest, TypeScript build | Backend tests and frontend validation |

- Redis is provisioned for future caching/queue needs.
- Current order ownership relies on PostgreSQL and a single-worker lock.

## USP / Showstopper

### Safety-first execution workflow

- **Strategy sub-ledgers:** each strategy has independent position and P&L tracking.
- **Broker reconciliation gate:** mismatches or unknown manual intraday activity block new entries.
- **Durable pre-submit intent:** persist order intent and reserve exposure before broker API call.
- **Partial fill support:** keep unfilled quantity reserved until broker confirms outcome.
- **Cancellation-race handling:** recheck broker trades before closing positions.
- **Fail closed:** timeout/invalid broker response -> `UNKNOWN`; reserve exposure and block entries.
- **Kill switch:** pause strategies, cancel entries, reconcile, close positions, verify flat state.
- **Data-quality gate:** stale or malformed feed cannot create a new entry.

### One-line pitch

> AlgoRhythm turns a strategy signal into an explainable, risk-checked, attributable, reconcilable, and recoverable execution workflow.

## Implementation Plan

### Completed

- 021 token configuration and login helper.
- Instrument-master download.
- Market and order WebSocket adapters.
- Binary frame decoding, heartbeat handling, reconnect/backoff.
- Live quote storage and 1-minute/5-minute candle construction.
- Two required strategies and recurring candle-momentum strategy.
- Registration, profile, subscribe/start/pause controls.
- Quantity and risk-limit configuration.
- Dashboard, orders, positions, risk, and kill-switch views.
- PostgreSQL migrations and Docker Compose setup.
- Single-worker lock.
- Durable order/execution accounting.
- Reconciliation, risk admission, loss limits, rate limits, and kill flow.
- Mocked backend tests and frontend production build configuration.

### Next steps

1. Add current 021 access token to ignored `.env`.
2. Bind token to correct local AlgoRhythm account ID.
3. Validate market/order sockets during market hours.
4. Capture live 021 Sandbox demo evidence.
5. Test partial fills, stale feed, reconnect, token expiry, cancellation race, and 15:15 close.
6. Align recurring-strategy hold-duration documentation/configuration.

### Current scope boundaries

- One bound 021 Sandbox account.
- NSE cash only.
- `INTRADAY` only.
- Three built-in strategies.
- Not yet included:
  - F&O
  - BSE execution
  - holdings/product conversion
  - multi-broker credential management
  - arbitrary user strategy code
- Charges: assumed 0.05% per-fill turnover until official schedule is available.
- Kill-switch target: 10 seconds; not a guarantee during broker outage or unresolved fills.

## Business Plan

### Target users

- Retail traders seeking transparent rule-based execution.
- Trading educators and student communities.
- Small research/advisory teams.

### Value proposition

- Convert repeatable rules into controlled execution workflows.
- Make orders, fills, P&L, risk, and emergency actions visible.
- Reduce errors caused by stale data, partial fills, disconnected dashboards, and manual square-off.

### Commercialisation path

| Stage | Offering | Revenue model |
| --- | --- | --- |
| Education / sandbox | Demo, paper trading, templates | Free adoption channel |
| Retail | Alerts, analytics, more controls/symbols | Monthly SaaS |
| Pro | Multi-strategy portfolio and exports | Premium subscription / team seats |
| Partner | Broker/educator/advisor dashboard | B2B licence and support |

### Go-to-market

- Start with 021 Sandbox demos and trading-education communities.
- Lead with safety, transparency, and reconciliation; never promise returns.
- Add broker integrations only after compliance and operational validation.
- Keep credentials server-side and maintain audit history.

### Future pilot metrics

- Reconciliation success rate.
- Order lifecycle completion rate.
- Signal-to-order acknowledgement time.
- Kill-switch completion time.
- Prevented risk violations and stale-feed blocks.
- Weekly active users and retention.
