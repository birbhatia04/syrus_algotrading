# AlgoRhythm

AlgoRhythm is a simulator-first algorithmic trading platform. It demonstrates safe strategy execution, risk enforcement, partial-fill accounting, per-strategy positions, P&L, recovery controls, and an account-level kill switch without connecting to a real broker.

**Modes:** `SIMULATOR` (default, deterministic) and `ALPACA_PAPER` (real US market-data streaming with Alpaca Paper orders). No live-money orders are supported.

## Run locally

### Option 1: Docker Compose (recommended)

Prerequisite: Docker Desktop must be running.

```bash
git clone https://github.com/birbhatia04/syrus_algotrading.git
cd syrus_algotrading
docker compose up --build
```

Open `http://localhost:5173`.

Docker starts PostgreSQL, the migration service, API, execution worker, Redis, and frontend. Use `Ctrl+C` to stop the stack, or `docker compose down` to stop and remove containers while retaining database data.

### Alpaca Paper Trading mode

The default Docker mode is the safe deterministic simulator. To use the Alpaca integration, first create or rotate **paper** credentials in Alpaca, then create a local `.env` file from `.env.example` and set:

```env
ENVIRONMENT=ALPACA_PAPER
ALPACA_API_KEY_ID=your_paper_key_id
ALPACA_API_SECRET_KEY=your_paper_secret_key
ALPACA_DATA_FEED=iex
ALPACA_DEFAULT_SYMBOL=AAPL
```

Never put these values in the frontend, README, or Git. Then restart the stack:

```bash
docker compose down
docker compose up --build
```

In the UI, subscribe a strategy to `AAPL`, start it, then select **Start Alpaca feed** on Overview. The worker receives Alpaca IEX trades over WebSocket, builds closed candles, evaluates strategies, submits approved market orders to the Alpaca **paper** endpoint, and reconciles confirmed Alpaca fills into AlgoRhythm.

### Option 2: Local development

Prerequisites: Python 3.9+ and Node.js 22+.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, start the execution worker:

```bash
cd backend
source .venv/bin/activate
PYTHONPATH=. python -m app.worker
```

In a third terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173` and create an account.

## How to test the platform

1. Open **Strategies**.
2. Subscribe to each of the three strategies and start them.
3. Return to **Overview**.
4. Click **Run full demo** to test the multi-strategy workflow.
5. Use the simulator controls to test full fill, partial fill, broker rejection, pending order, stale data, and cancellation race cases.
6. Open **Orders**, **Executions**, **Positions**, and **Risk** to inspect results.
7. Use **Stop & flatten account** last. Once all positions are flat, use **Resume trading** to restart safely.

## Implemented

- Account registration, login, logout, and account-scoped data access.
- Three built-in strategies: moving-average crossover, RSI mean reversion, and five-minute breakout.
- UTC-aligned 1-minute and 5-minute OHLC candle generation from simulated ticks.
- Closed-candle strategy evaluation and duplicate-signal suppression.
- Deterministic simulator with full, partial, rejected, pending, stale-feed, and cancellation-race scenarios.
- Durable order lifecycle, confirmed-execution trade records, execution replay protection, and weighted-average accounting.
- Separate strategy positions and P&L alongside aggregate account exposure.
- Realized P&L, unrealized P&L, charges, and net P&L calculations.
- Per-strategy daily-loss, position-size, and rolling order-rate controls.
- Persisted timeline showing ticks, candles, signals, risk decisions, orders, fills, and cancellations.
- Controlled virtual market stream that advances one simulated minute every two seconds.
- Account kill switch that stops strategies, cancels open orders, closes attributed positions, and requires explicit resume after a safe reconciliation.
- FastAPI backend, SQLAlchemy models, PostgreSQL Docker setup, Alembic migration bootstrap, React/Vite frontend, and backend invariant tests.
- Alpaca Paper Trading adapter: backend-only API credentials, US market-data WebSocket feed, AAPL candle construction, paper-order submission, idempotent activity-fill reconciliation, and USD display.

## Not implemented yet

- Verified 021 sandbox or broker integration. Official API documentation and sandbox credentials are required before implementing its authentication, market data, order submission, fills, charges, cancellation, and reconciliation mappings.
- Real-money order routing. Alpaca integration is deliberately locked to its paper endpoint.
- Production deployment, secret manager, monitoring, backups, alerting, and audit/outbox infrastructure.
- PostgreSQL row-level locking for concurrent multi-worker risk admission.
- Redis-backed live price cache and rate-counter acceleration.
- Durable cross-process WebSocket event delivery; the dashboard currently refreshes authoritative REST snapshots after commands.
- Historical backtesting, charting, portfolio analytics, trade export, and a complete admin console.

## Verification

```bash
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q

cd ../frontend
npm run typecheck
npm run build
```
