# AlgoRhythm

AlgoRhythm is an algorithmic-trading workspace for strategy controls, risk checks, orders, positions, and account safety controls.

## Current integration

`UPSTOX_SANDBOX` is the Indian-market integration mode:

- **Upstox Analytics Token** supplies live Indian market data through Market Data Feed V3.
- **Upstox Sandbox Token** receives test-only orders. No real-money order route exists in this project.
- The default instrument is `INFY` (`NSE_EQ|INE009A01021`), configurable through environment variables.

## Run locally

1. Create a local `.env` from `.env.example`.
2. For deterministic simulator-only development, keep `ENVIRONMENT=SIMULATOR`.
3. For live Upstox prices and sandbox orders, set:

   ```env
   ENVIRONMENT=UPSTOX_SANDBOX
   UPSTOX_ANALYTICS_TOKEN=your_upstox_analytics_token
   UPSTOX_SANDBOX_ACCESS_TOKEN=your_upstox_sandbox_token
   UPSTOX_DEFAULT_SYMBOL=INFY
   UPSTOX_DEFAULT_INSTRUMENT_KEY=NSE_EQ|INE009A01021
   ```

4. Start the application:

   ```bash
   docker compose up --build
   ```

5. Open `http://localhost:5173`, register or sign in, subscribe/start strategies, and select **Start Upstox feed**.

## What is implemented

- User registration, confirmed password, onboarding details, and editable profile.
- Strategy subscription, start/pause controls, risk limits, account kill switch, and resume flow.
- Orders, execution accounting, positions, realised/unrealised P&L, and risk-event history.
- Deterministic simulator mode for repeatable testing.
- Upstox Market Data Feed V3 worker integration and Upstox Sandbox order adapter.

## Still required before using Upstox mode

- Create the two Upstox tokens and store them only in ignored `.env`.
- Confirm the desired Indian instrument key for each symbol before adding multi-symbol support.
- Verify the sandbox lifecycle responses with your own token; the sandbox is deliberately never switched to a live-money endpoint.
