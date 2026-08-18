# algo-trading-system (SUPER_TRADEMAN) — Project Brief

**Read this file completely before changing any code.** It is the handoff document for a fresh
agent or IDE session with no prior context. Everything marked **VERIFIED** was independently
re-run against the exact committed code on 2026-08-17 (see §0). Everything marked **INFERENCE**
is reasoning that has not been separately tested.

---

## 0. Repo state

- **GitHub**: `https://github.com/mubasharali24428-crypto/SUPER_TRADEMAN` — **private**.
- **Local path**: `/Users/user/untitled folder 2/algo-trading-system` (note the space in the
  parent directory — quote it in every shell command).
- Single commit on `main`: `645c4ff "initial commit: crypto algo trading system"`, 39 files,
  working tree clean.

```bash
git clone https://github.com/mubasharali24428-crypto/SUPER_TRADEMAN.git
```

## 1. What this is

A crypto algorithmic trading system in Python 3.12, currently **backtest-only**. There is no live
execution, no broker connection, and no money at risk. The intent is a bot that can eventually
trade autonomously, gated by a deterministic risk layer.

## 2. Stack and layout

| Path | Role |
|---|---|
| `src/trading/config.py` | pydantic-settings, reads `.env` |
| `src/trading/indicators.py` | Shared `atr()` and `donchian()` — both strategies and the backtester's trailing stop measure volatility the same way |
| `src/trading/data/crypto.py` | ccxt OHLCV fetch w/ exponential backoff → Postgres `ohlcv` table |
| `src/trading/strategy/crypto.py` | Mean reversion. `generate_signal()` |
| `src/trading/strategy/trend.py` | **New.** Donchian breakout / trend following. `generate_trend_signal()` |
| `src/trading/risk/models.py` | Frozen dataclasses: `Signal`, `Position`, `RiskConfig`, `AccountState`, `ApprovedOrder`, `ExitSignal`, `ApprovedExit` |
| `src/trading/risk/engine.py` | `RiskEngine` — deterministic gate. The security boundary. |
| `src/trading/backtest/engine.py` | Event-loop backtester: slippage/commission, trailing ATR stop, Wilson CI, exact binomial, bootstrap |
| `src/trading/db/` | asyncpg pool, redis client |
| `scripts/` | `validate_crypto_backtest.py`, `diagnose_{trades,regime,volatility}.py`, `backtest_symbol.py`, `compare_strategies.py`, `evaluate_trend.py`, `trend_attribution.py` |
| `tests/` | 61 tests (`test_indicators.py` and `test_strategy_trend.py` are new) |

Tooling: `uv`. Postgres 16 + Redis 7 via `docker-compose.yml`. Deps: `ccxt>=4.4`, `asyncpg>=0.29`,
`redis>=5.0`, `pydantic-settings>=2.4`; dev: `pytest>=8.0`, `pytest-asyncio>=0.24`
(`asyncio_mode=auto`).

### Run it

```bash
docker compose up -d && uv sync && cp .env.example .env && uv run pytest
```

**VERIFIED:** `59 passed, 2 failed` when Postgres is down. **Both** failures
(`tests/test_db.py::test_postgres_connects`, `tests/test_crypto_data.py::test_ingest_known_historical_btc_data`)
are the same root cause — `asyncpg.create_pool` refused on `localhost:5432` — not code defects.
`test_db.py::test_redis_connects` passes on its own; Redis is not implicated. With
`docker compose up -d` all 61 should pass. `scripts/*.py` that fetch live data cache candles under
`~/.cache/algo-trading-system/`; once warm, re-runs finish in seconds with no network calls.

---

## 3. Architectural invariants — DO NOT BREAK THESE

Deliberate design decisions, not accidents. Re-verified against the current code, quoted exactly.

1. **The Risk Engine is the sole authority on opening risk.** Every signal passes through
   `RiskEngine.evaluate()` — pure, deterministic, no probabilistic judgment.

2. **`ApprovedOrder`/`ApprovedExit` can only be constructed by the Risk Engine.**
   `risk/models.py`: `_ISSUER = object()` — a module-private sentinel. Both dataclasses'
   `__post_init__` raise `PermissionError` unless `issuer is _ISSUER`. `risk/engine.py` is the
   only place in the tree that imports `_ISSUER` or constructs either type. Identity comparison
   against a private singleton cannot be forged by an equal-looking value from outside the module.
   **Do not weaken this to make testing easier.**

3. **Position sizing is derived, never chosen**: `position_size = (equity × risk_pct) / risk_per_unit`.

4. **`RiskConfig.risk_pct` is hard-capped at 2%** in `__post_init__`. Business decision, not a
   tuning knob.

5. **Any LLM / sentiment / anomaly component may only ever propose closing risk.** The single
   channel is `ExitSignal → RiskEngine.evaluate_exit_signal() → ApprovedExit`. `ExitSignal` and
   `ApprovedExit` carry no `entry_price`, `side`, `stop`, `target`, or size field — there is
   nothing in either type a caller could use to open or resize a position even by mistake.
   **Do not add an LLM path that opens positions.**

6. **`evaluate_exit_signal()` deliberately skips the kill switch, drawdown, and loss-limit
   checks.** Confirmed: it runs exactly two gates (position exists; `confidence >=
   min_exit_confidence`) and none of the four halt conditions `evaluate()` runs. Those rules exist
   to block *new* risk; blocking a de-risking action with a risk-halting rule would be
   self-defeating. This asymmetry is intentional.

7. **R-multiple is always measured against the risk taken at entry**, never a moved stop.
   `backtest/engine.py` stores `initial_stop_price` separately from the mutable `stop_price` used
   for exit triggering; `_close_trade` computes `risked` from the former. Trailing a stop must
   never inflate the R of the trade that produced it.

8. **The backtester's stop-fill assumption is deliberately pessimistic**: when stop and target are
   both touched inside one bar, it assumes the stop filled.

---

## 4. Where this started

A single mean-reversion strategy (z-score entry, RSI confirmation, trend-regime filter) lost money
both in-sample and out-of-sample on 270 days of BTC/USDT 1h: train −17.81%, test −2.74%, 46 trades.
Four root causes were diagnosed (see git history / prior session for the full derivation):

- **D1**: the regime filter's trend threshold (`0.02`) sat *above* the 99th percentile of observed
  trend slopes — it blocked only 3.9% of signals and was effectively inert.
- **D2**: the risk engine's global `min_reward_risk=2.0` applied to a strategy whose R:R reduces to
  `sigma_20/sigma_50` — a volatility-expansion filter, not a quality filter, that adversely
  selected for the exact regime where mean reversion fails.
- **D3**: stops sized from `pstdev` of raw price levels rather than ATR, on a 1h timeframe where
  stops sit under 1% of price — friction (slippage + both commissions) ate ~0.33R per trade,
  realizing −1.33R stop-outs instead of −1.0R.
- **D4**: `breakeven_p` was hardcoded at 1/3 (clean 2:1, zero costs), when the realized payoffs
  implied a true breakeven near 40%.

## 5. What was fixed, and what it changed — **VERIFIED against committed code**

| Defect | Fix | File |
|---|---|---|
| D1 | `trend_strength_threshold` 0.02 → **0.008** (between observed p50=0.41% and p90=1.21%) | `strategy/crypto.py:31` |
| D2 | `min_reward_risk` decoupled per strategy — each `RiskEngine` takes its own `RiskConfig` instance; mean reversion runs at `1.0`, trend at `2.0` | `RiskEngine(RiskConfig(min_reward_risk=...))`, e.g. `scripts/compare_strategies.py` |
| D3 | Stops now sized from ATR (`indicators.atr`), not `pstdev` of price levels; strategies take raw candles, not closes | `strategy/crypto.py`, `strategy/trend.py`, `indicators.py` |
| D4 | `_breakeven_win_rate(trades)` derives breakeven from realized avg win/loss R (`avg_loss / (avg_win + avg_loss)`); `1/3` is now only a degenerate-case fallback | `backtest/engine.py:237-259` |

Plus two additions:
- **`strategy/trend.py`** — Donchian breakout (55-bar entry, ATR stop/target, Turtle-style
  defaults, deliberately untuned).
- **Trailing ATR stop** in the backtester (`BacktestConfig.trail_atr_mult`) — trend-following
  profit lives in the right tail, which a fixed target would cut off.

### The decisive finding: timeframe controls whether friction kills the edge — **VERIFIED**

Same signal, same code, only the candle timeframe changes:

| Timeframe | avg R, no costs | avg R, w/ 0.1%/side costs | friction per trade | stop as % of price |
|---|---|---|---|---|
| 1h | +0.065 | **−0.076** | 0.142R | 1.77% |
| 4h | +0.101 | +0.007 | 0.094R | 3.88% |
| **1d** | +0.488 | **+0.441** | **0.046R** | **10.69%** |

Lower timeframes mean tighter stops mean larger notional per unit of risk, so fixed round-trip
friction (~0.2% of notional) dominates the edge. On daily bars the stop is wide enough that
friction is nearly irrelevant.

### Repaired mean reversion (1h) — **still no edge, VERIFIED**

BTC/USDT 1h, 270d, 70/30 split, `min_reward_risk=1.0`, ATR stops: train **−6.4%**, test
**−13.6%** (win rate 29.2% vs a 52.6% breakeven at these realized payoffs). The category error
(D2) was real, but repairing it did not create an edge at this timeframe — the strategy premise
itself doesn't survive 1h crypto fees.

### Daily-timeframe trend following — **the one configuration with a real edge, VERIFIED reproducible**

5 assets (BTC, ETH, BNB, SOL, XRP), 7 years, 1d candles, 70/30 split, `trail_atr_mult` swept over
`{None, 2, 3, 5, 8}`:

```
trail  split  trades   win%    avgR   total R  ret/sym  exit mix (stop/target/time)
 None  train    143   37.1%  +0.435    +62.3   +13.01%   87/ 52/  0/ 4
 None  test      49   42.9%  +0.598    +29.3    +6.05%   26/ 19/  0/ 4
  2.0  train    204   42.2%  +0.277    +56.6   +11.97%  174/ 30/  0/ 0
  2.0  test      75   44.0%  +0.203    +15.2    +3.08%   67/  7/  0/ 1
  3.0  train    179   41.9%  +0.362    +64.8   +13.84%  131/ 46/  0/ 2
  3.0  test      62   46.8%  +0.357    +22.1    +4.51%   48/ 13/  0/ 1
  5.0  train    166   36.7%  +0.416    +69.0   +14.60%  108/ 54/  0/ 4
  5.0  test      52   44.2%  +0.592    +30.8    +6.48%   32/ 18/  0/ 2
  8.0  train    155   37.4%  +0.424    +65.8   +13.77%   97/ 54/  0/ 4
  8.0  test      50   46.0%  +0.654    +32.7    +6.82%   26/ 19/  0/ 5
```

Profitable at **every** swept configuration, in and out of sample — not a single-config fluke.
Bootstrap terminal-return distribution (`trail_atr_mult=5.0`): train p5/p50/p95 = +32%/+72%/+113%;
test = +11%/+33%/+55%.

**The test that actually matters — long/short attribution**, because 3 of the 5 assets ran
+400% to +5000% buy-and-hold over the window, so an all-long system could look "profitable" while
just being levered beta:

```
TEST aggregate: 52 trades
  LONG  n=31  win 41.9%  avgR +0.667  totalR +20.7
  SHORT n=21  win 47.6%  avgR +0.481  totalR +10.1
```

**Both books are profitable out-of-sample.** This is not beta in a bull market — SOL/USDT fell
−57.7% over its test window and the strategy still returned −5.50% on it (a small loss, not a
blowup), while every other asset's short book was flat-to-positive. That is the strategy trading
trend in both directions, as designed.

### Market-context sanity check — **VERIFIED**

Kaufman efficiency ratio (net move / total path traveled — near 1 is a clean trend, near 0 is
chop), median over 30-bar blocks, 1d/7y: BTC 0.184, ETH 0.187, BNB 0.173, SOL 0.206, XRP 0.183.
Consistent with "trending enough for breakout entries to have real signal," not degenerate.

---

## 6. Decision: daily-timeframe trend following is the one to build on

**Selected:** Donchian breakout, 1d candles, 5-asset portfolio (BTC/ETH/BNB/SOL/XRP), trailing
ATR stop. It is the only configuration in this codebase with a demonstrated out-of-sample edge
that isn't explained by market beta, and it is robust across a 5-point sweep of its main knob
rather than being a single lucky setting.

**Not selected, and why:**
- **Mean reversion (any timeframe tested)** — repairing the four defects did not produce an edge;
  1h loses train and test even after the fix. Kept in the repo (it's a clean reference
  implementation and the tests guard its documented behavior) but not the deploy candidate.
- **4h/1h trend following** — friction dominates (see table above); results are weak or negative
  and, on 4h, unstable across the trail-mult sweep (unlike 1d, where every setting wins).
- **Cointegration / pairs trading** — still the right idea for a market-neutral complement, but it
  needs a two-leg backtester (`run_backtest` currently prices one instrument at a time). Deferred
  until the portfolio-backtest gap below is closed, since that's most of the same work.
- **Cross-sectional momentum, funding-rate carry** — reasonable future additions once the
  single-position backtester limitation (§7) is fixed; not attempted yet.

---

## 7. Known defects and gaps

- **`run_backtest` still holds one position at a time.** The risk engine's portfolio heat,
  correlation guard, and per-asset-class position limits are fully implemented and unit-tested in
  isolation (`tests/test_risk_engine.py`) but have **never been exercised end-to-end by a
  portfolio backtest** — every backtest run so far is single-instrument. This is the top blocker
  before running the 5-asset trend portfolio for real: right now "5 assets" in §5 means 5
  *separate* single-asset backtests summed, not one account with shared heat/correlation limits.
- **No funding-rate modelling** for perp shorts held for hours/days.
- **No gap/liquidity modelling** — stops assume a fill at exactly `stop_price` plus fixed
  slippage.
- **`_breakeven_win_rate` estimates its null from the same sample it tests** — the resulting
  p-value is a sanity check, not a rigorous test (this is called out in the function's own
  docstring). `bootstrap_trade_returns` is the more trustworthy statistic.
- **One 70/30 split, not walk-forward.** With five trail-mult configurations tried against the
  same test window, there is real multiple-testing risk that a formal walk-forward /
  purged-K-fold setup and a deflated Sharpe correction would quantify.
- **No live execution layer**: no order placement, no reconciliation, no position tracking against
  an exchange, no restart/recovery semantics.
- `.env` is gitignored, contains only local Postgres/Redis URLs. No exchange keys anywhere in the
  repo.
- Minor, unfixed: `~/.cache/algo-trading-system/` has duplicate 4h cache files under two naming
  conventions (`*_4h_5y.json` and `*_4h_5.0y.json`) from earlier script iterations — harmless,
  just delete the stale one if it's confusing.

---

## 8. Rules for whoever works on this next

1. **Do not add a live-execution path** without an explicit instruction from the owner. When it is
   built: paper trading first, then testnet, then real money — never skip a rung.
2. **Never weaken the Risk Engine to make a strategy look better.** If a strategy only works with
   the guardrails loosened, the strategy is the problem.
3. **Report backtest results with the sample-size verdict and breakeven win rate attached** — both
   are computed by the codebase specifically to prevent self-deception. Quote them.
4. **Every strategy or parameter change must be re-validated out-of-sample**, and the number of
   configurations tried should be tracked — that count is what a deflated-Sharpe correction needs,
   and the 1d-trend result in §5 is currently one un-corrected grid search, not a proof.
5. **A negative-expectancy bot is worse than no bot.** Mean reversion stays in the repo as a tested
   reference, not as something to build features on top of.

## 9. Suggested next task

Close the biggest gap named in §7: rebuild `run_backtest` to hold a portfolio of concurrent
positions across the 5 selected assets, sharing one `AccountState` so the risk engine's heat cap,
correlation guard, and per-class position limit actually gate something. Re-run the daily-trend
backtest as one 5-asset portfolio instead of five summed single-asset runs, and compare the
portfolio-level max drawdown against the naive sum in §5 — correlated crypto drawdowns should make
the portfolio number worse than the sum implies, which is exactly what the heat cap exists to
catch. That's a well-defined diff with an unambiguous pass/fail check (portfolio backtest runs,
heat cap actually rejects an over-limit signal in at least one test), and it's required before the
pairs-trading strategy from §6 becomes buildable.
