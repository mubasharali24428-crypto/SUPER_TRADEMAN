# algo-trading-system (SUPER_TRADEMAN) — Project Brief

**Read this file completely before changing any code.** It is the handoff document for a fresh
agent or IDE session with no prior context. Everything marked **VERIFIED** was independently
re-run against the exact committed code — §4–§6 on 2026-08-17, §0–§2 and §7–§10 on **2026-08-21**.
Everything marked **INFERENCE** is reasoning that has not been separately tested.

---

## 0. Repo state — **VERIFIED 2026-08-21**

- **GitHub**: `https://github.com/mubasharali24428-crypto/SUPER_TRADEMAN` — **public** as of
  2026-08-21 (was private; made public so it could be cited in a research application).
- **Local path**: `/Users/user/AG PROJ1/algo-trading-system` (note the space in `AG PROJ1` —
  quote it in every shell command).
- `main` at `897e049`, **197 files**, three commits: `645c4ff` initial, `67162a9` doc refresh,
  `3a1de50` the full risk/synthetic/execution/ops tree, `897e049` README and pytest fix.
- **`354 passed, 2 skipped`** for the full suite.

> ### Two traps that have each cost a session already
>
> 1. **A stale decoy copy exists at `/Users/user/untitled folder 2/algo-trading-system`.** It is
>    the old 39-file skeleton, it has the *same two early commits* and the *same `origin` remote*,
>    and it has none of `synthetic/`, `execution/`, `stats/`, `security/`, `ops/`,
>    `infrastructure/`, or the advanced `risk/` modules. It is easy to mistake for this project
>    and conclude that half the codebase does not exist. **Do not use it.**
> 2. **Console scripts in `.venv/bin/` break when this folder is moved.** They are `#!/bin/sh`
>    wrappers holding an absolute path, so after a move `.venv/bin/pytest` silently execs the
>    *old* location's Python and fails with `ModuleNotFoundError: No module named 'numpy'`. Fix:
>    `rm -rf .venv && uv sync`. Diagnose with `head -2 .venv/bin/pytest`.

```bash
git clone https://github.com/mubasharali24428-crypto/SUPER_TRADEMAN.git
```

## 1. What this is

A crypto algorithmic trading system in Python 3.12. **Backtest and simulation only**: no live
execution, no broker connection, no money at risk, and no exchange keys anywhere in the repo.
`config/deployment_config.yml` defines `PAPER` / `SHADOW` / `LIVE_RESTRICTED` modes, but nothing
runs against a real venue. The intent is a bot that can eventually trade autonomously, gated by a
deterministic risk layer that no probabilistic component can talk its way past.

Beyond the strategy/backtest core there is now a **synthetic adversarial market layer** used to
stress-test behaviour under liquidity attacks, and an **execution and ops layer** built for a
future live path that is deliberately not wired up.

## 2. Stack and layout — **VERIFIED 2026-08-21**

| Path | Role |
|---|---|
| `src/trading/config.py` | pydantic-settings, reads `.env` |
| `src/trading/indicators.py` | Shared `atr()`, `donchian()`, `log_return_correlation()` |
| `src/trading/data/` | `crypto.py` ccxt OHLCV → Postgres; `quality.py`, `staleness.py` |
| `src/trading/strategy/` | `crypto.py` mean reversion; `trend.py` Donchian breakout |
| `src/trading/risk/models.py` | Frozen dataclasses + the `_ISSUER` construction token (§3.2) |
| `src/trading/risk/engine.py` | `RiskEngine` — deterministic gate. The security boundary. |
| `src/trading/risk/garch.py` | `GARCHVolatilityModel`, `GARCHForecastResult` |
| `src/trading/risk/hmm_regime.py` | `HMMRegimeClassifier`, `HMMRegimeResult` |
| `src/trading/risk/evt.py` | `EVTRiskEngine`, `EVTRiskResult` |
| `src/trading/risk/copula.py` | `CopulaDependencyEngine`, `CopulaDependencyResult` |
| `src/trading/risk/survival.py` | `SurvivalEngine`, `SurvivalTier`, `AccountSurvivalStatus` |
| `src/trading/risk/` (rest) | `portfolio_circuit_breaker.py`, `correlation.py`, `capital_allocator.py`, `portfolio_risk.py` |
| `src/trading/backtest/engine.py` | Event-loop backtester: slippage/commission, trailing ATR stop, Wilson CI, exact binomial, bootstrap |
| `src/trading/backtest/portfolio.py` | `run_portfolio_backtest()` — **concurrent** positions across assets, so heat/correlation/class limits actually gate (§7) |
| `src/trading/backtest/` (rest) | `funding.py`, `impact.py`, `strategies.py` |
| `src/trading/stats/` | `pbo.py` `compute_pbo()`; `cross_validation.py` `generate_cpcv_splits()`, `CPCVConfig`; `effective_trials.py` |
| `src/trading/synthetic/` | Adversarial layer: `ecology.py`, `chaos_injector.py`, `stale_protection.py`, `strategy_defense.py`, `lob.py`, `venue.py`, `oms_engine.py`, `event_ingestor.py`, `regime_validator.py`, `portfolio_governor.py`, `agents/`, plus forex/stocks/polymarket engines |
| `src/trading/execution/` | `oms.py`, `reconciler.py`, `state_machine.py`, `shadow.py`, `tca.py`, `venue_adapter.py`, `chase.py`, `outbox.py` |
| `src/trading/daemon/heartbeat.py` | `TradingHeartbeatDaemon`, `HeartbeatCycleResult` |
| `src/trading/learning/` | `graph.py` `LearningGraph` (needs `networkx`), `policy.py` |
| `src/trading/security/` | `audit_ledger.py`, `secrets_manager.py` (reads env vars only) |
| `src/trading/{observability,ops,infrastructure}/` | Logging, metrics, alerting, health service, drills, HA lock, shutdown, state recovery |
| `src/trading/db/` | asyncpg pool, redis client |
| `scripts/` | Diagnostics, backtest drivers, plus `preflight_check.py`, `promote_mode.py`, `kill_switch_drill.py`, report generators, `deploy.sh`, `rollback.sh` |
| `tests/` | **354 passing tests** across risk, synthetic, execution, stats, ops, security, infrastructure |
| `web/`, `monitoring/`, `docs/` | Dashboard, Prometheus/Grafana config, deployment and incident runbooks |

Tooling: `uv`. Postgres 16 + Redis 7 via `docker-compose.yml`.
Deps: `pydantic-settings>=2.4`, `asyncpg>=0.29`, `redis>=5.0`, `ccxt>=4.4`, `arch>=6.0`,
`hmmlearn>=0.3`, `copulas>=0.7`, `scipy>=1.14`, `networkx>=3.0`; dev: `pytest>=8.0`,
`pytest-asyncio>=0.24` (`asyncio_mode=auto`, `pythonpath=["."]`).

### Run it

```bash
docker compose up -d && uv sync && cp .env.example .env && uv run pytest
```

**VERIFIED 2026-08-21:** `354 passed, 2 skipped`. The 2 skips need Postgres on `localhost:5432`;
they are environment-dependent, not code defects. Redis is not implicated.

`pythonpath = ["."]` in `[tool.pytest.ini_options]` is load-bearing: several tests import helpers
from `scripts/` at the repo root, and the `pytest` console script (unlike `python -m pytest`) does
not put rootdir on `sys.path`. Without it a bare `uv run pytest` fails collection on a fresh clone.

`scripts/*.py` that fetch live data cache candles under `~/.cache/algo-trading-system/`; once warm,
re-runs finish in seconds with no network calls.

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

> **Read §7 before quoting any number in this section.** These results were verified as reproduced
> on 2026-08-17, but they were produced by five *separate* single-asset backtests summed, not by
> `run_portfolio_backtest()`, and no PBO or purged-CV correction has been applied to them. They are
> provisional, not a proof.

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

## 7. Known defects and gaps — **UPDATED 2026-08-21**

### Closed since the 2026-08-17 revision

- **The single-position backtester blocker is closed in code.**
  `backtest/portfolio.py::run_portfolio_backtest()` holds concurrent positions across assets and
  routes every entry through `RiskEngine.evaluate()` with live `log_return_correlation()` values,
  so the portfolio heat cap, correlation guard, and per-asset-class limits now gate something
  real. **VERIFIED 2026-08-21:** 7 tests pass across `tests/test_backtest_portfolio.py` and
  `tests/trading/backtest/test_portfolio_purge.py`.
- **Machinery for the multiple-testing problem now exists**: `stats/pbo.py::compute_pbo()`,
  `stats/cross_validation.py::generate_cpcv_splits()` (combinatorial purged CV), and
  `stats/effective_trials.py`.

### Still open

- **The §5 headline numbers were NOT produced by the portfolio backtester.** They remain five
  *separate* single-asset backtests summed. The shared-risk machinery exists and is tested, but
  the 5-asset trend result has not been regenerated through it. Doing that is the next real task,
  and the numbers should be expected to change once heat and correlation limits actually bind.
- **The statistics modules have not been applied to the §5 result either.** It is still one
  un-corrected five-point grid search against a single 70/30 split. `compute_pbo()` and
  `generate_cpcv_splits()` exist precisely to fix this and have not been pointed at it yet. Until
  then, quote §5 as provisional.
- **No funding-rate modelling** applied to perp shorts held for hours/days (`backtest/funding.py`
  exists; it is not wired into the §5 runs).
- **No gap/liquidity modelling** — stops assume a fill at exactly `stop_price` plus fixed slippage.
- **`_breakeven_win_rate` estimates its null from the same sample it tests** — the resulting
  p-value is a sanity check, not a rigorous test (called out in the function's own docstring).
  `bootstrap_trade_returns` is the more trustworthy statistic.
- **No live execution layer is wired up.** `execution/` contains an OMS, reconciler, order state
  machine, shadow mode, and TCA, but nothing places an order against a real venue and there is no
  restart/recovery path in use. Treat `execution/` as built-but-dormant, not as working plumbing.
- **`learning_graph.jsonl` is a runtime artifact**, gitignored as of 2026-08-21.
- `.env` is gitignored and holds only local Postgres/Redis URLs. **No exchange keys anywhere in
  the repo or in any commit** — verified 2026-08-21 by scanning every blob in history for
  credential patterns before the repo was made public.
- Minor, unfixed: `~/.cache/algo-trading-system/` has duplicate 4h cache files under two naming
  conventions (`*_4h_5y.json` and `*_4h_5.0y.json`) from earlier script iterations — harmless,
  just delete the stale one if it's confusing.
- Minor: stale `__pycache__` entries still report tracebacks under the pre-move
  `/Users/user/Downloads/Project MK/...` path. Cosmetic only; clear with
  `find . -name __pycache__ -prune -exec rm -rf {} +`.

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
6. **Do not describe a module as working because it exists and imports.** §10 was once written as
   a list of finished infrastructure when none of it was wired into a result, which cost a later
   session a great deal of confusion. State separately: does it exist, do its tests pass, and is
   it actually used by the number being quoted. Those are three different claims.
7. **Verify which copy you are in before editing.** See the §0 trap box.

## 9. The synthetic adversarial layer — **VERIFIED 2026-08-21**

`src/trading/synthetic/` simulates a hostile market so strategy and risk behaviour can be tested
against liquidity attacks rather than only against historical candles. This is the part of the
system with the least precedent elsewhere, so read `ecology.py` before changing anything here.

`ecology.py` broadcasts a `LiquidityEvent` (`QUOTE_WITHDRAWAL`, `AGGRESSIVE_SWEEP`, `BOOK_RELOAD`)
carrying a `depletion_ratio` against a rolling-median depth baseline (`LiquidityBaselineTracker`,
300s window). `SyntheticAgentRegistry` fans each event out to registered agents:

- `ReactiveMarketMakerAgent` — widens its spread when `depletion_ratio > herd_sensitivity`
  (default `0.40`), modelling a liquidity-vacuum cascade.
- `ToxicFlowPredatorAgent` — fires an `IOC_SWEEP` when `depletion_ratio > aggression_threshold`
  (default `0.50`), sweeping `min(current_depth * 0.80, max_sweep_size)`.

**Know the ceiling of this design.** Every adversary here is rule-based and triggers on a fixed
numeric threshold applied to a single scalar. None of them can invent a new attack, combine
vectors, adapt to how the defense responds, or exploit any variable other than the one they watch.
That is fine as a regression harness and dishonest as a claim of adversarial robustness. Treat
these agents as a *baseline*, not as proof the system survives real adversaries.

Also here: `chaos_injector.py`, `stale_protection.py`, `strategy_defense.py` (defense state
machine), `lob.py`, `venue.py`, `oms_engine.py`, `regime_validator.py`, `portfolio_governor.py`,
`agents/{base_agent,orchestrator,personas}.py`, and separate forex/stocks/polymarket engines.
Covered by the `tests/synthetic/` suite.

## 10. Advanced probabilistic and autonomous infrastructure

**Verification status, stated precisely (2026-08-21):** every module below exists, imports
cleanly, and has a passing dedicated test file — `tests/test_{garch,hmm_regime,evt,copula,survival}.py`
is **15 passed**, and `tests/test_heartbeat.py` passes within the full 354. The declared libraries
are genuinely installed and used (`arch` 8.0.0, `hmmlearn` 0.3.3, `copulas` 0.14.1, `scipy` 1.18.0).
The *mathematical descriptions below have not been independently re-derived line by line*; they
describe intent. Read the module before relying on a specific formula.

None of these are wired into the §5 headline result.

1. **GARCH(1,1) volatility sizing** (`risk/garch.py`, `GARCHVolatilityModel` →
   `GARCHForecastResult`): fits conditional volatility
   $\sigma_t^2 = \omega + \alpha \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2$ on log-returns via `arch`,
   producing a one-step-ahead annualized forecast used to scale position risk before volatility
   spikes. `Signal` carries a `garch_vol_scale` field for this.

2. **3-state HMM regime classifier** (`risk/hmm_regime.py`, `HMMRegimeClassifier` →
   `HMMRegimeResult`): unsupervised Gaussian HMM over return/volatility vectors via `hmmlearn`,
   intended to separate trending-bull, volatile-bear, and choppy-sideways regimes, exposing state
   posteriors and a transition matrix.

3. **EVT tail-VaR** (`risk/evt.py`, `EVTRiskEngine` → `EVTRiskResult`): peaks-over-threshold
   Generalized Pareto fit on empirical loss tails via `scipy.stats.genpareto`, giving a fat-tailed
   99% Tail-VaR / expected shortfall rather than a Gaussian one.

4. **Copula joint tail-risk guard** (`risk/copula.py`, `CopulaDependencyEngine` →
   `CopulaDependencyResult`): quantifies lower-tail co-dependence $\lambda_L$ across asset pairs,
   for capping cluster risk when assets crash together instead of independently.

5. **Survival state engine** (`risk/survival.py`, `SurvivalEngine`, `SurvivalTier`,
   `AccountSurvivalStatus`): moves the account through `NORMAL` / `CAUTION` / `SURVIVAL` /
   `COOLDOWN` on drawdown, consecutive losses, GARCH volatility spikes, and EVT tail risk.

6. **Autonomous heartbeat daemon** (`daemon/heartbeat.py`, `TradingHeartbeatDaemon` →
   `HeartbeatCycleResult`): async Think → Act → Observe → Reflect loop that writes trade outcomes
   to `LearningGraph` (`learning/graph.py`, requires `networkx`), updates Bayesian Normal-Normal
   posteriors, and trains an offline policy-gradient contextual bandit (`learning/policy.py`).
   **This loop does not place real orders** and §3.1 and §3.5 still bind it: nothing it learns can
   mint an `ApprovedOrder`.

