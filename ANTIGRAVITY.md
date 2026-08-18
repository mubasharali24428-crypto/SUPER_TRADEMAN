# algo-trading-system — Project Brief

**Read this file completely before changing any code.** It is the handoff document for a fresh
agent or IDE session with no prior context. Everything marked **VERIFIED** was measured by running
the code on 2026-08-17; everything marked **INFERENCE** is reasoning that has not been separately
tested.

---

## 1. What this is

A crypto algorithmic trading system in Python 3.12, currently **backtest-only**. There is no live
execution, no broker connection, and no money at risk. The intent is a bot that can eventually
trade autonomously, gated by a deterministic risk layer.

**Location:** `/Users/user/untitled folder 2/algo-trading-system`
**Not a git repository.** There is no history. If you break something, there is no `git checkout`.
Initialise git before substantial work:

```bash
cd "/Users/user/untitled folder 2/algo-trading-system" && git init && git add -A && git commit -m "baseline"
```

## 2. Stack and layout

| Path | Role |
|---|---|
| `src/trading/config.py` | pydantic-settings, reads `.env` |
| `src/trading/data/crypto.py` | ccxt OHLCV fetch w/ exponential backoff → Postgres `ohlcv` table |
| `src/trading/strategy/crypto.py` | The strategy. Mean reversion. `generate_signal()` |
| `src/trading/risk/models.py` | Frozen dataclasses: `Signal`, `Position`, `RiskConfig`, `AccountState`, `ApprovedOrder`, `ExitSignal`, `ApprovedExit` |
| `src/trading/risk/engine.py` | `RiskEngine` — deterministic gate. The security boundary. |
| `src/trading/backtest/engine.py` | Event-loop backtester + statistics (Wilson CI, exact binomial, bootstrap) |
| `src/trading/db/` | asyncpg pool, redis client |
| `scripts/` | `validate_crypto_backtest.py`, `diagnose_{trades,regime,volatility}.py`, `backtest_symbol.py` |
| `tests/` | 45 tests |

Tooling: `uv` (not pip/poetry). Postgres 16 + Redis 7 via `docker-compose.yml`. Deps: `ccxt`,
`asyncpg`, `redis`, `pydantic-settings`; dev: `pytest`, `pytest-asyncio` (asyncio_mode=auto).

### Run it

```bash
docker compose up -d && uv sync && uv run pytest
```

**VERIFIED:** `43 passed, 2 failed` when Postgres is down. The 2 failures (`tests/test_db.py`,
`tests/test_crypto_data.py`) are connection errors, not code defects. With `docker compose up -d`
all 45 should pass. `scripts/*.py` hit the live Binance public API and need network; each takes
1–3 minutes. There is no API key anywhere and no authenticated endpoint is used.

---

## 3. Architectural invariants — DO NOT BREAK THESE

These are deliberate design decisions, not accidents. They are the reason the project is safe to
work on. An agent that "simplifies" them has made the system worse.

1. **The Risk Engine is the sole authority on opening risk.** Every signal passes through
   `RiskEngine.evaluate()`. It is pure and deterministic — no probabilistic judgment, no model
   inference, no randomness. Every check is a hard rule.

2. **`ApprovedOrder` and `ApprovedExit` can only be constructed by the Risk Engine.** They check
   `issuer is _ISSUER` (a private module-level sentinel) in `__post_init__` and raise
   `PermissionError` otherwise. This makes "bypass the risk engine" a runtime error rather than a
   code-review question. **Do not weaken this to make testing easier.**

3. **Position sizing is derived, never chosen.** `position_size = (equity × risk_pct) / risk_per_unit`.
   A strategy cannot request a size.

4. **`RiskConfig.risk_pct` is hard-capped at 2%** in `__post_init__`. Raising this cap is a
   business decision for the human owner, not a tuning knob.

5. **Any LLM / sentiment / anomaly component may only ever propose closing risk.** The single
   channel is `ExitSignal` → `RiskEngine.evaluate_exit_signal()` → `ApprovedExit`. It cannot open
   a position, cannot resize one, cannot pick a direction. This is the whole safety story for
   putting a language model near a trading system. **Do not add an LLM path that opens positions.**

6. **`evaluate_exit_signal()` deliberately does NOT check the kill switch, drawdown, or loss
   limits.** Those rules exist to block *new* risk. Blocking a de-risking action with a
   risk-halting rule would be self-defeating. This asymmetry is intentional; the docstring says so.

7. **The backtester's stop-fill assumption is deliberately pessimistic**: when stop and target are
   both touched inside one bar, it assumes the stop filled. Keep it.

---

## 4. Current state: the strategy does not work

### The strategy as written

`generate_signal()` in `src/trading/strategy/crypto.py`. Single-asset mean reversion on BTC/USDT 1h:

- **Entry:** price ≥ 2 standard deviations from its trailing 20-bar mean (`z_threshold=2.0`).
- **Regime filter:** skip if `|100-bar SMA slope over a 20-bar shift| > 2%`.
- **RSI confirmation:** long only if RSI(14) < 30; short only if RSI(14) > 70.
- **Stop:** `1.5 × pstdev(last 50 closes)` from entry (`stop_std_mult`, `stop_window`).
- **Target:** the 20-bar mean.
- Risk engine then requires **reward:risk ≥ 2.0** (`RiskConfig.min_reward_risk`).

### Measured results — **VERIFIED**, 270d BTC/USDT 1h, 6480 candles (2025-11-21 → 2026-08-18), 70/30 split

| | TRAIN (in-sample) | TEST (out-of-sample) |
|---|---|---|
| total return | **-17.81%** | **-2.74%** |
| max drawdown | 17.81% | 5.99% |
| Sharpe (per-bar) | -0.032 | -0.010 |
| win rate | 25.81% (95% CI 13.7–43.2%) | 53.33% (95% CI 30.1–75.2%) |
| avg R multiple | -0.52 | -0.14 |
| trades | 31 | 15 |
| bootstrap p5/p50/p95 | -29.8% / -17.8% / -5.1% | -12.5% / -2.9% / +7.3% |

**Losing in-sample and out-of-sample. Both confidence intervals are enormous. n=46 total trades
over 9 months is not enough to conclude anything.** Do not read the 53% test win rate as
encouraging — it is 15 trades and still a negative return.

### Exit-reason breakdown (train, n=31) — **VERIFIED**

| reason | n | wins | total P&L | avg R |
|---|---|---|---|---|
| stop | 21 (68%) | **0** | -29,673 | **-1.33** |
| time_stop | 5 | 3 | +1,997 | +0.38 |
| target | 5 | 5 | +9,864 | +1.98 |

### Gate firing rates over the full window — **VERIFIED**

```
raw z>=2 signals            1148
blocked by regime filter      45   (3.9%)
passed R:R>=2 gate           145   (13.1% of those reaching it)
R:R distribution            p50=1.14  p90=2.22  p99=3.73  max=8.29
sigma20/sigma50 ratio       p50=0.61  p90=1.14  max=1.57   (needs >=1.5 to pass R:R)
|trend slope| at signals    p50=0.406%  p90=1.209%  p99=2.967%   (gate fires at 2.000%)
```

---

## 5. Diagnosis — four concrete defects

### D1. The regime filter is effectively inert — **VERIFIED**

It blocks **3.9%** of signals. The 2% threshold sits above the 99th percentile of the actual slope
distribution at signal time (p99 = 2.967%, p90 = 1.209%). Its stated purpose — "don't mean-revert
against a strong trend" — is not being served. The Jan–Mar 2026 stretch in the trade log is
long-after-long stopped out while fading a downtrend the filter waved through.

**Fix:** recalibrate the threshold to roughly the p70–p80 of the observed distribution (~0.6–0.9%),
or replace the measure entirely — ADX, Hurst exponent, or Kaufman efficiency ratio are all better
trend/chop discriminators than an SMA-slope percentage.

### D2. The R:R ≥ 2.0 gate is adversely selecting — **VERIFIED mechanism, INFERENCE on magnitude**

This is the most important finding. Work through the geometry:

```
reward = |target - entry| = |z| × sigma_20        (target IS the 20-bar mean)
risk   = 1.5 × sigma_50
R:R    = |z| × sigma_20 / (1.5 × sigma_50) >= 2   =>   sigma_20 / sigma_50 >= 1.5  (at z=2)
```

The gate is not selecting for trade quality. It is selecting for **short-window volatility having
just exploded relative to the 50-bar baseline** — median ratio is 0.61, and you need ≥1.5, which is
essentially the observed maximum (1.57). So 87% of signals are rejected, and the ~13% that survive
are drawn from the extreme tail of volatility expansion. **Volatility expansion is precisely the
regime in which mean reversion fails.** The risk gate is systematically picking the worst trades.

Deeper point: **requiring R:R ≥ 2 on a mean-reversion strategy is a category error.** Mean
reversion is structurally a high-win-rate / low-R:R trade. Trend following is the low-win-rate /
high-R:R trade. A single global `min_reward_risk` cannot serve both. It must become per-strategy.

### D3. Transaction costs eat ~0.33R per round trip — **VERIFIED**

A stop-out should be exactly -1.0R by definition. Realized stops average **-1.33R**. The gap is
exit slippage plus both commissions, which `risked` (computed as
`position_size × |entry_fill - stop_price|`) does not include. With 0.1% slippage and 0.1%
commission per side, round-trip friction is ~0.4% of *notional* — and notional here is roughly
70–110× the risked amount, because the stop is under 1% of price. Small stops mean large notional
means friction dominates.

**Fix:** widen stops (ATR-based), lengthen the timeframe (4h/1d), use maker limit entries instead
of taker fills, or accept that 1h mean reversion on 0.4% round-trip cost is not viable.

### D4. The backtest tests against the wrong null hypothesis — **VERIFIED**

`run_backtest(breakeven_p=1/3)` assumes 33.3% is breakeven, which is only true at a clean 2:1
payoff with zero costs. Using the *realized* payoffs (+1.98R win, -1.33R loss), true breakeven is
`1.33 / (1.98 + 1.33) = 40.2%`. Every reported `win_rate_p_value` is therefore **optimistic**. The
train win rate of 25.8% is not 7 points short of breakeven, it is 14 points short.

**Fix:** compute `breakeven_p` from realized average win/loss R rather than passing a constant.

---

## 6. Where to go — candidate strategies, ranked

### Tier 0 — repair before replacing (do this first)

Cheapest work, and without it every comparison against a new strategy is biased.

1. Fix `breakeven_p` (D4). One function. Makes all subsequent evaluation honest.
2. Move `min_reward_risk` from global `RiskConfig` to a per-strategy parameter (D2).
3. Replace level-`pstdev` stops with **ATR(14)**. `statistics.pstdev` on *raw price levels* is
   dominated by trend drift, not noise — it is the wrong dispersion measure for both the entry
   z-score and the stop. Consider z-scoring *returns*, or Bollinger %B, for the entry.
4. Recalibrate or replace the regime filter (D1).
5. Re-run. If it is still negative, the strategy premise is dead and Tier 1 is the answer.

### Tier 1 — established strategies that fit this architecture

**1. Pairs trading / statistical arbitrage (cointegration spread reversion) — best fit**
Mean-revert the *spread* between two cointegrated assets (ETH/BTC, SOL/ETH, or a basket) instead of
a single asset's price. Single-asset price reversion has weak theoretical justification; spread
reversion has a real one — the legs share a common factor, so the spread has an economic reason to
be stationary. Engle–Granger or Johansen for the cointegration test, Kalman filter for a
time-varying hedge ratio, Ornstein–Uhlenbeck half-life to set holding period and time stop.
Market-neutral, so it survives the directional regime currently causing the losses. Reuses almost
all existing machinery.

**2. Time-series momentum / trend following (Donchian, MA crossover, TSMOM) — most robust**
The most consistently documented systematic edge across asset classes (Moskowitz–Ooi–Pedersen 2012)
and replicated in crypto. Two things make it a natural fit here: it is *complementary* — the
current regime filter already identifies trending periods and sits them out, so a trend module
would trade exactly the regime the current strategy avoids; and trend following genuinely does
produce R:R ≥ 2, so the risk engine's existing 2.0 floor — a category error for mean reversion —
is correct for it as-is.

**3. Cross-sectional momentum on an alt basket**
Rank ~20–50 liquid pairs by trailing 7–30d return, long the top quintile, short the bottom,
rebalance weekly. Liu, Tsyvinski & Wu (2022), *Common Risk Factors in Cryptocurrency* (Journal of
Finance) documents crypto market/size/momentum factors. Side benefit that matters a lot here:
**it generates far more trades, which fixes the n=46 statistical-power problem.**

**4. Funding-rate carry / cash-and-carry basis**
Long spot, short perpetual, harvest funding. This is a *structural* edge, not a statistical one —
funding is paid by leveraged longs and has been positive most of the time — so it does not decay
the way a price-pattern edge does. Market-neutral. Risks are counterparty, margin management and
liquidation, not alpha decay, which suits a risk-engine-first design. Requires perp market access
and careful margin modelling the current backtester does not have.

### Tier 2 — higher effort

5. **Regime-switching ensemble.** A classifier (Hurst, efficiency ratio, HMM, realized-vol
   quantile) routes between the mean-reversion and trend modules. The natural end state of 1+2.
6. **Meta-labeling** (López de Prado, *Advances in Financial Machine Learning*). Keep the primary
   signal; train a secondary classifier to decide *whether to take* each one. Directly targets
   "right idea, poor precision" — which is what the 53% test win rate hints at. Brings triple-barrier
   labeling (which this backtester already implements: stop / target / time-stop) and purged K-fold CV.
7. **Order-flow / microstructure**: order-book imbalance, CVD, liquidation-cascade fading. Needs L2
   data and a real-time pipeline.
8. **Market making (Avellaneda–Stoikov).** Needs fee tier, latency, inventory management. Out of
   scope at this stage.

### Tier 3 — methodology (arguably the highest-value work of all)

Once several strategies are being tried, **multiple-testing inflation becomes the main risk** — it
is what kills most retail quant projects, not bad ideas.

- **Walk-forward analysis** instead of one 70/30 split.
- **Purged + embargoed K-fold CV** to prevent label leakage across adjacent bars.
- **Deflated Sharpe Ratio / Probability of Backtest Overfitting** (Bailey & López de Prado) to
  discount for the number of configurations tried.
- **Multi-asset, multi-timeframe** universe for sample size.
- **Realistic fills**: maker vs taker fees, perp funding, slippage as a function of order size
  against real book depth.
- **Portfolio backtesting.** `run_backtest()` currently holds **one position at a time**, so the
  risk engine's portfolio heat, correlation guard and per-class position limits are never exercised
  by any backtest. That is a meaningful untested surface.

---

## 7. Known defects and gaps

- **`breakeven_p` default is wrong** (D4) — `src/trading/backtest/engine.py`.
- **Backtester is single-position**, so heat/correlation/max-position logic is untested end to end.
- **No funding-rate modelling.** Shorts held for hours on perps pay or receive funding; ignored.
- **No gap/liquidity modelling.** Stops assume a fill at exactly `stop_price` plus fixed slippage.
- **`fetch_ohlcv_with_backoff` returns `None`** if the loop completes without returning — currently
  unreachable, but it is an implicit contract violation waiting to happen.
- **`ingest_ohlcv` calls `ensure_schema` after fetching**, so a schema failure wastes the API call.
- **No live execution layer at all**: no order placement, no reconciliation, no position tracking
  against an exchange, no restart/recovery semantics.
- `.env` is gitignored and contains only local Postgres/Redis URLs. No secrets, no exchange keys.

---

## 8. Rules for whoever works on this next

1. **Do not add a live-execution path** without an explicit instruction from the owner. When it is
   built: paper trading first, then testnet, then real money — and never skip a rung.
2. **Never weaken the Risk Engine to make a strategy look better.** If a strategy only works with
   the guardrails loosened, the strategy is the problem.
3. **A negative-expectancy bot is worse than no bot.** Solve the edge problem before building
   features on top of it. Execution, dashboards and deployment are all premature right now.
4. **Report backtest results with the sample-size verdict attached.** The codebase already computes
   Wilson CIs, exact binomial p-values, a bootstrap distribution and an explicit
   `sample_size_verdict` string. They exist to prevent self-deception. Quote them.
5. **Every strategy change must be re-validated out-of-sample**, and the number of variants tried
   must be tracked — that count is what the deflated Sharpe correction needs.
6. **`git init` first** (see §2). There is currently no undo.

## 9. Suggested first task

Tier 0, items 1–4, then re-run `scripts/validate_crypto_backtest.py` and compare against the
baseline table in §4. That is a small, well-defined diff with an unambiguous pass/fail check, and
it tells you whether the mean-reversion premise is salvageable or whether to move to Tier 1.
