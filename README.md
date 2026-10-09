# AlgoRhythm — 021-03 Algo Trading Platform

A FastAPI, SQLAlchemy and React platform for the **021 developer sandbox**. This release trades **NSE cash stocks with INTRADAY orders**, using the official October 2026 API guide.

The three 021 strategies run on one bound sandbox account. Local users have separate platform logins; only the account selected by BROKER_021_ACCOUNT_ID can use the configured broker token. External/manual INTRADAY activity is detected and blocks entries because its strategy attribution is unknown. Separate CNC delivery activity is outside the platform's INTRADAY ledger and does not block it.

## Setup

The ignored **.env** file has been created in the repository root. Fill:

- BROKER_021_USERNAME: your UCC, such as HACK1234, not your email.
- BROKER_021_PASSWORD: the password chosen in the 021 app.
- BROKER_021_ACCESS_TOKEN: optional if you already have a current token.
- BROKER_021_ACCOUNT_ID: the local account ID shown by GET /api/v1/me; initially 1.

Your platform password and your 021 password are separate. Do not commit credentials.

### Obtain the daily token

From the repository root, install backend dependencies if needed, then run the login helper:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python.exe -m app.brokers.login_021
~~~

The helper reads credentials from .env (or prompts when blank), writes the access token to .env, and prints only its expiry. **Logging in again revokes the previous token.** API and worker reuse the same token; neither logs in automatically. Refresh it after expiry at 05:00 IST, then restart both services. You can also paste an existing token into .env and skip the login command.

### Docker

From the repository root:

~~~powershell
docker compose up --build
~~~

Open http://localhost:5173. Docker uses a new named database volume, algorhythm-021-db, to keep old simulator history separate. The old volume is not deleted. Docker overrides the local SQLite database URL with PostgreSQL.

After editing the token, recreate API and worker so both receive the same value:

~~~powershell
docker compose up -d --force-recreate api worker
~~~

### Local development

Run these from backend in separate terminals:

~~~powershell
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

~~~powershell
..\.venv\Scripts\python.exe -m app.worker
~~~

Then from frontend:

~~~powershell
npm ci
npm run dev
~~~

The supplied .env uses a separate local algorhythm_021.db. PostgreSQL is recommended for deployment. One execution worker is enforced with a PostgreSQL advisory lock or a SQLite process file lock.

### First run

1. Fill credentials, obtain one token, and start API, worker and frontend.
2. Register your local platform account and complete the profile.
3. On Overview, check Token, Account bound, Worker and Reconciled. The worker downloads today's instrument CSV.
4. On Strategies, select a supported NSE symbol and subscribe to all three strategies.
5. Configure quantity, entry side for the timed strategy, and risk limits. Default quantity is 1; the instrument's lot and freeze limits still apply.
6. Enable 021 trading on Overview, then start the strategies.
7. Orders are submitted only when a strategy condition is satisfied in the trading session. Starting after 09:16 skips that day's timed entry; it does not fabricate a trade.

Changing ENVIRONMENT to SIMULATOR enables the retained deterministic demo. Use a separate database for it. Simulator endpoints are rejected in 021 mode.

## Exact reference strategies

All times are IST; all entries use **market orders** (price = 0, book = RL, product = INTRADAY, validity = Day). Order side is represented by signed quantity at the API boundary. Internal prices and P&L are rupees; 021 integer prices are converted from/to paise at the boundary.

| Strategy | Entry | Exit and sizing |
| --- | --- | --- |
| Required time entry | One entry attempt on the first fresh quote in 09:15:00–09:15:59. BUY by default; SELL configurable. No late catch-up. | Default 1 share. At 15:15, cancel entry remainders, reconcile fills, then close the strategy's actual attributed quantity. |
| Required open breakout | BUY at LTP ≥ today's exchange open × 1.01; SELL at LTP ≤ open × 0.99. Open comes from the full market packet. | Default 1 share, one entry attempt per IST day. Long target/stop = weighted entry fill × 1.05/0.95; short = × 0.95/1.05. Levels rounded to nearest exchange tick, half up. The platform watches prices, cancels pending entry remainder, then submits the opposite market order. Force exit at 15:15. |
| Recurring candle momentum | On every newly closed 1-minute candle while flat, add its percentage body to the latest 5-minute candle's percentage body. A non-negative combined bias buys; a negative bias sells. | Default 1 share. Flat-only entry; no pyramiding. Exit on an opposite 1-minute candle, after 1 minute from the latest fill, or at 15:15. Re-enter on a later closed candle after the exit is confirmed, capped at 6 entry cycles per trading day. |

Quantities are configurable while paused and flat. Strategy code emits intentions; the worker checks platform risk limits before persisting and submitting them. Pausing entries retains protective exits and reconciliation.

## Candles and live feed

- One market websocket subscribes in full mode; an orders websocket triggers REST reconciliation.
- Binary frames use the documented big-endian layouts. Heartbeats and concatenated market packets are handled; malformed frames cause reconnection.
- 1-minute and 5-minute OHLC candles are built from received market updates, aligned in UTC (also aligned to IST minute boundaries).
- A tick in a later bucket closes the previous open candle. Closed candles are immutable; stale/out-of-order updates do not rewrite them. Missing buckets are not filled with invented ticks.
- Cumulative volume differences are used instead of repeatedly adding last-trade quantity. Initial snapshot volume is unknown for the current candle.
- Replayed identical snapshots are ignored. Quote, candles and position marks are committed together.
- Reconnect obtains a fresh ephemeral key and resubscribes. The orders socket does not replay, so REST catches up after reconnect and restart.
- **021 provides sampled snapshots at most every 300 ms, not every exchange trade.** These candles represent the received feed and cannot guarantee exchange-tape OHLC completeness. New entries require a trade timestamp and receipt time no more than 15 seconds old.

## Orders, accounting and recovery

- The worker commits SUBMITTING intent and quantity reservation **before** POST /orders.
- Successful submission stores the broker order ID. Only confirmed GET /orders/{id}/trades rows create executions.
- The order websocket has no trade ID, so it is a reconciliation signal, never a second fill source.
- Executions are deduplicated by exchange, IST trading day and trade ID. Fill, charges, strategy position, daily ledger and order filled quantity are one transaction.
- Daily assumed charges are rebuilt from the immutable execution ledger during reconciliation, including after normalization of a malformed sandbox trade timestamp.
- Partial fills retain the unfilled reservation. Rejection or confirmed cancellation releases it.
- GET /orders, GET /trades and GET /portfolio/positions are compared with local ledgers. Inconsistent snapshots, external INTRADAY activity, unsupported INTRADAY positions and missing fills block entries until resolved.
- Separate strategy sub-ledgers preserve opposing long/short positions; their sum must equal the broker INTRADAY net position. CNC delivery activity is isolated by product and ignored by this INTRADAY engine. The live sandbox has returned zero, stale, and oversized `squareOffQuantity` values, so the worker treats reconciled `netQuantity` as authoritative and never uses the unreliable field to size an order. Closing each attributed strategy quantity therefore sums to the account close quantity without crossing through flat or assigning the whole account position to every strategy.
- The guide describes tradeTime as Unix seconds, while the live sandbox has been observed returning the same 1980-based epoch used by its market feed. The worker detects the closer plausible timestamp, normalizes it to UTC, and keeps the raw trade ID as the deduplication anchor.
- A cancellation acknowledgement is not a cancellation fill guarantee. The worker waits for REST confirmation and incorporates any racing fill before sizing the close.
- Restart never blindly resubmits SUBMITTING or UNKNOWN orders. HTTP 5xx, transport failure or an invalid response after POST produces UNKNOWN; entry exposure stays reserved and the account is blocked.

**Ambiguous submission limitation:** the guide documents no client idempotency key or query-by-client-ID endpoint. Exactly-once remote order placement cannot be proven across a timeout. Use the Overview recovery form to link an UNKNOWN local order to a broker ID after reviewing 021. The API checks side, quantity, token, product, pre-submission order snapshot and timestamp. An unidentifiable outcome remains blocked; the platform does not guess or automatically mark it rejected.

## Risk and kill switch

- Per-strategy maximum position checks include signed worst-case exposure from all outstanding orders, including UNKNOWN and cancellation-pending orders.
- The order-rate limit uses a rolling 60 seconds and counts submitted orders, including broker rejections.
- Daily loss = today's realized P&L + current unrealized P&L − today's assumed charges. The ledger rolls at IST midnight. A breached limit latches for the rest of that day, cancels entry remainders and closes that strategy.
- Close-only orders must reduce the attributed position, cannot cross zero, and cannot exceed position quantity left after other close reservations. They bypass entry loss/rate limits, while quantity and ownership checks still apply.
- User pause/risk/kill controls serialize with risk approval and order reservation through an account lock.
- The kill API durably pauses every strategy immediately. The worker prioritizes cancellation, REST catch-up and closing orders, including when a strategy raises an exception. Large closes are split at the instrument freeze limit.
- The dashboard reports HALTED only after local positions, known open orders and broker reconciliation confirm flat. It shows the measured flatten time.
- **The target is 10 seconds, not an unconditional guarantee during a broker outage, unresolved POST or lack of fills.** A timeout becomes NEEDS_ATTENTION; entries stay disabled and recovery continues. The tested cancellation-race fixture confirms flat in 2 simulated seconds; no live latency claim is made.
- Built-in strategies are trusted code; this release does not execute arbitrary user-supplied Python or sandbox CPU-infinite strategy loops.

## Charges

As requested, the retained assumption is **0.05% of each fill's turnover**, applied to both buys and sells, rounded to 4 decimal rupees. ASSUMED_CHARGE_RATE defaults to 0.0005.

The supplied API guide has no published charges table. The UI labels these charges as assumed. Net P&L is exact for the stored fills under this assumption; it is **not yet validated against the organiser's official charge schedule**.

## Verification

The baseline implementation previously passed 36 mocked backend tests, a frontend production build and the SQLite Alembic migration through revision 0002. Re-run these checks after local changes. Broker tests use mocked responses.

From backend:

~~~powershell
..\.venv\Scripts\python.exe -m pytest tests -q
~~~

From frontend:

~~~powershell
npm run build
~~~

Tests cover binary frame offsets, sampled snapshot replay, wire units, timeout ambiguity, restart behavior, partial fills, deduplication, opposing strategies, cancellation races, stale prices, rolling rate limits, daily loss latching/reset, close-only validation, mandatory strategy conditions, candle confirmation, broken strategy handling and kill timeout reporting. HTTP calls in the tests are mocked; no 021 credentials are required.

## Current scope and pending live checks

- Supported execution: one bound 021 account, NSE cash, INTRADAY, three strategies. Additional local users can register but cannot share the configured broker ledger.
- F&O/BSE execution, product conversion, holdings, arbitrary custom code, and multi-broker credential management are outside this release.
- Historical/recorded-day endpoints are mentioned by the problem statement but not specified by the supplied API guide; they have not been invented.
- Continuous live operation and the 10-second kill target still need verification with your sandbox token during market hours.
- Official charges remain pending. Live schemas, symbol mappings and actual sandbox failure behavior have not been verified with your credentials.
