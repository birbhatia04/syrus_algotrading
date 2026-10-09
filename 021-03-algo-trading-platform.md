# Algo Trading Platform

## The challenge

**Build an algo trading platform on the 021 developer APIs where risk limits hold even when the strategy itself is buggy.**

An AI assistant will write you a moving-average crossover bot in minutes. That is not what we are scoring. We are scoring the platform around the strategy: what happens when an order is half-filled or the server restarts mid-trade, and whether a runaway strategy can be stopped. Most retail algo losses come from these, not from bad strategy ideas.

|                       |                                                                                                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| You get               | 021 Developer Sandbox: historical candles, a live price feed, recorded market days, and order APIs on a simulated account, with realistic failures switched on |
| You may use           | Any language, any framework, any database, and any AI coding tool                                                                                              |
| You will be judged on | Hidden test scenarios, a live change request on the day, and a technical viva, not on the slides or on how profitable your strategy is                         |

## What you're building

The platform has four levels. Each level builds on the one before it. A rock-solid L2 beats a shaky L4: we score depth before breadth.

At kickoff, publish a **reference strategy** in your README with exact rules: when it enters, when it exits, and how much it buys. We might also provide our own strategy for every team to implement, so we can check your results against the correct answer.

1. **L1: Strategy subscription and candle creation.** Build a platform where users can create an account and subscribe to any strategy. Build 1-minute and 5-minute candles from raw ticks, and use them in at least one of your strategy's entry conditions.
2. **L2: Live trading.** Place orders against the sandbox, track fills, handle partial fills and rejections, and keep the strategy's view of its position in line with the account. Output the exact list of trades, with net P&L after charges from the published charges table.
3. **L3: Risk controls.** Per-strategy limits on maximum loss for the day, maximum position size and maximum orders per minute. A kill switch that stops all strategies, cancels open orders and closes positions within 10 seconds. Limits are enforced by the platform, not by the strategy's own code.
4. **L4: Several strategies on one account.** Run at least three strategies at once on the same account. Each keeps its own P&L and position, even when one is long and another is short the same stock.

Create a dashboard where users can see which strategies they are subscribed to and their live P&L.


## The sandbox misbehaves on purpose

The 021 Developer Sandbox behaves like a real broker on a busy day. The full API reference and keys are shared at kickoff. These behaviours are switched on during development and during judging. Expect issues such as rate limits, random server errors where a call returns HTTP 500 or 503, order timeouts, and so on.


## Automatic disqualification
Claims in the README the code does not back up.

---

*Disclaimer: We may make minor changes to this problem statement on the day of the hackathon. Any changes will be communicated to all participating teams.*
