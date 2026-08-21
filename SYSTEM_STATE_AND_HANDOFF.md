# SUPER_TRADEMAN: Complete System State, Architectural Specifications, & Next-Step Decision Brief

> **Target Audience:** Quantitative trading developers, algorithmic systems architects, risk engineers, and AI/LLM models evaluating the next development phase of this repository.
>
> **Project Repository:** `SUPER_TRADEMAN` (`algo-trading-system`)  
> **Last Updated:** August 2026  
> **Current Operating Mode:** Backtest & Statistical Simulation (Paper/Live Trading Staged Next)

---

## Table of Contents
1. [Executive Summary & Core Philosophy](#1-executive-summary--core-philosophy)
2. [Sovereign Architectural Invariants (Non-Negotiable Rules)](#2-sovereign-architectural-invariants-non-negotiable-rules)
3. [Chronological Evolution & Diagnostics (How We Got Here)](#3-chronological-evolution--diagnostics-how-we-got-here)
   - [Phase 1: Mean Reversion Baseline & Diagnostic Autopsy (D1–D4)](#phase-1-mean-reversion-baseline--diagnostic-autopsy-d1d4)
   - [Phase 2: The Timeframe/Friction Finding & Daily Trend Breakout Edge](#phase-2-the-timeframefriction-finding--daily-trend-breakout-edge)
   - [Phase 3: Multi-Asset Portfolio Backtester (Closing the Single-Position Gap)](#phase-3-multi-asset-portfolio-backtester-closing-the-single-position-gap)
   - [Phase 4: Advanced Statistical & Tail-Risk Engineering](#phase-4-advanced-statistical--tail-risk-engineering)
   - [Phase 5: Autonomous Heartbeat Daemon & Bayesian Learning Graph](#phase-5-autonomous-heartbeat-daemon--bayesian-learning-graph)
   - [Phase 6: High-Sample Statistical Benchmarking (10,000+ Trades)](#phase-6-high-sample-statistical-benchmarking-10000-trades)
   - [Phase 7: Real-Time Interactive Web Cockpit & UI Terminal](#phase-7-real-time-interactive-web-cockpit--ui-terminal)
4. [Full Codebase Architecture & File Catalog](#4-full-codebase-architecture--file-catalog)
5. [Mathematical & Statistical Formulations](#5-mathematical--statistical-formulations)
6. [Known Gaps, Remaining Deficiencies, & Technical Debt](#6-known-gaps-remaining-deficiencies--technical-debt)
7. [Specific Inquiry Framework for LLM Recommendations](#7-specific-inquiry-framework-for-llm-recommendations)

---

## 1. Executive Summary & Core Philosophy

**SUPER_TRADEMAN** is an institutional-grade, quantitative cryptocurrency algorithmic trading and risk-management system written in **Python 3.12+**. 

The system operates on the principle of **asymmetric capital defense**:
1. **Mathematical Edge Over Prediction:** Strategies do not attempt to "forecast" micro-market turns; they capture structural market premiums (e.g., Donchian momentum breakouts on daily horizons) where the signal-to-friction ratio is overwhelmingly positive.
2. **Sovereign Deterministic Risk Gate:** All trading decisions proposed by strategies, bandits, or external LLMs are strictly advisory. No order can be placed without clearing a cryptographically/identity-guarded, deterministic `RiskEngine`.
3. **Automaton-Style Survival State Architecture:** The trading account dynamically throttles risk across four operational tiers (`NORMAL`, `CAUTION`, `SURVIVAL`, `COOLDOWN`) driven by GARCH conditional volatility, Hidden Markov Model (HMM) regime probabilities, and Extreme Value Theory (EVT) tail-risk indicators.

---

## 2. Sovereign Architectural Invariants (Non-Negotiable Rules)

These invariants are hardcoded into the system core and must **never** be weakened or bypassed:

| # | Invariant Rule | Implementation Mechanism | Purpose |
|---|---|---|---|
| **1** | **Sole Authority on Risk** | `RiskEngine.evaluate()` is the single gatekeeper for opening risk. | Eliminates ad-hoc or emotion-driven trade execution. |
| **2** | **Cryptographic/Identity Order Issuance** | `ApprovedOrder` and `ApprovedExit` can *only* be constructed by `RiskEngine`. Both classes enforce `issuer is _ISSUER` (`_ISSUER = object()` private sentinel in `risk/models.py`). | Prevents forged orders from external modules or strategies. |
| **3** | **Strict Derived Position Sizing** | $\text{Size} = \frac{\text{Equity} \times \text{RiskPct}}{\text{RiskPerUnit}}$, where $\text{RiskPerUnit} = |\text{Entry} - \text{Stop}|$. | Guarantees exact R-unit loss normalization across varying volatilities. |
| **4** | **Hard Capital Risk Cap** | `RiskConfig.risk_pct` is hard-capped at $\le 2.0\%$ per trade in `__post_init__`. | Mathematical protection against gambler's ruin. |
| **5** | **LLM / Anomaly Exit-Only Channel** | External AI/LLM signals can only emit `ExitSignal` $\to$ `evaluate_exit_signal()` $\to$ `ApprovedExit`. No `entry_price`, `side`, or `size` fields exist in exit models. | An AI model can propose de-risking, but can **never** initiate new risk. |
| **6** | **Asymmetric Risk Gate on Exits** | `evaluate_exit_signal()` ignores drawdown, daily loss limits, and kill switches. | De-risking must never be blocked by risk-halting rules. |
| **7** | **Pessimistic Stop-Fill Invariant** | If both stop price and profit target are breached within the same bar, backtesters unconditionally assume the stop was hit first. | Eliminates look-ahead and over-optimistic backtest bias. |
| **8** | **R-Multiple Preserved from Entry** | R-multiple is always calculated as $\frac{\text{Exit} - \text{Entry}}{|\text{Entry} - \text{InitialStop}|}$, never moving with a trailing stop. | Prevents trailing stops from artificially inflating trade R statistics. |

---

## 3. Chronological Evolution & Diagnostics (How We Got Here)

### Phase 1: Mean Reversion Baseline & Diagnostic Autopsy (D1–D4)

The project began with a 1-hour timeframe mean-reversion strategy (Z-score + RSI + regime filter) tested on 270 days of BTC/USDT 1h data. In backtests, it lost money consistently: **Train: −17.81%, Test: −2.74% (46 trades)**.

A systematic diagnostic uncovered four fatal defects:
* **D1 (Inert Regime Filter):** The trend threshold was set at `0.02` (above the 99th percentile of real trend slopes), filtering out only 3.9% of bars. Fixed by recalibrating to `0.008` (between p50 and p90).
* **D2 (Category Error on R:R):** The risk engine required global `min_reward_risk = 2.0`. For mean-reversion, R:R reduces to $\sigma_{20}/\sigma_{50}$ (volatility expansion), which actively selected the exact regime where mean-reversion fails. Fixed by decoupling `min_reward_risk` per strategy (1.0 for mean-reversion, 2.0 for trend).
* **D3 (Price Standard Deviation Stops):** Stops were sized from `pstdev(prices)` rather than ATR. On 1h bars, tight stops (~0.8% price distance) meant exchange friction (slippage + 0.1% fees) ate ~0.33R per trade. Fixed by migrating all stop calculations to ATR.
* **D4 (Hardcoded Breakeven):** `breakeven_p` was hardcoded to $0.333$ (assuming zero friction), while real payoffs required $\sim 40\%$. Fixed by dynamically computing breakeven win rate from realized payoffs: $\text{BE} = \frac{\overline{\text{Loss}}}{\overline{\text{Win}} + \overline{\text{Loss}}}$.

**Crucial Verdict on Mean Reversion:** Even after all four defects were corrected, 1h mean reversion produced **Train: −6.4%, Test: −13.6% (win rate 29.2% vs 52.6% required breakeven)**. The conclusion was decisive: *crypto exchange friction structurally destroys tight-stop mean reversion on lower timeframes.*

---

### Phase 2: The Timeframe/Friction Finding & Daily Trend Breakout Edge

We systematically varied the timeframe on identical signals with $0.1\%$/side fee + slippage:

| Timeframe | Avg R (No Friction) | Avg R (With Friction) | Friction Drag per Trade | Stop Distance (% Price) |
|---|---|---|---|---|
| **1h** | +0.065 R | **−0.076 R** (Unprofitable) | 0.142 R | 1.77% |
| **4h** | +0.101 R | +0.007 R (Breakeven) | 0.094 R | 3.88% |
| **1d** | +0.488 R | **+0.441 R** (Highly Profitable) | **0.046 R** | **10.69%** |

#### Multi-Asset Out-of-Sample Validation (Daily Donchian Breakout)
Across **5 major crypto assets** (BTC, ETH, BNB, SOL, XRP) over **7 years (2019–2026)** on daily bars with a 70/30 Train/Test split:

```
trail_mult  split    trades   win%     avg R    total R   ret/sym   exit mix (stop/target/time)
 None       train      143   37.1%   +0.435 R    +62.3 R   +13.01%    87 /  52 / 0 / 4
 None       test        49   42.9%   +0.598 R    +29.3 R    +6.05%    26 /  19 / 0 / 4
  2.0       train      204   42.2%   +0.277 R    +56.6 R   +11.97%   174 /  30 / 0 / 0
  2.0       test        75   44.0%   +0.203 R    +15.2 R    +3.08%    67 /   7 / 0 / 1
  3.0       train      179   41.9%   +0.362 R    +64.8 R   +13.84%   131 /  46 / 0 / 2
  3.0       test        62   46.8%   +0.357 R    +22.1 R    +4.51%    48 /  13 / 0 / 1
  5.0       train      166   36.7%   +0.416 R    +69.0 R   +14.60%   108 /  54 / 0 / 4
  5.0       test        52   44.2%   +0.592 R    +30.8 R    +6.48%    32 /  18 / 0 / 2
  8.0       train      155   37.4%   +0.424 R    +65.8 R   +13.77%    97 /  54 / 0 / 4
  8.0       test        50   46.0%   +0.654 R    +32.7 R    +6.82%    26 /  19 / 0 / 5
```

#### Long/Short Out-of-Sample Attribution (Beta Isolation)
To confirm profitability was not merely riding the multi-year crypto bull market:
```
TEST Aggregate (52 trades OOS):
  LONG  Book: n = 31 | Win Rate: 41.9% | Avg R: +0.667 R | Total R: +20.7 R
  SHORT Book: n = 21 | Win Rate: 47.6% | Avg R: +0.481 R | Total R: +10.1 R
```
Both books are independently profitable out-of-sample. During periods where individual assets dropped $>50\%$ (e.g. SOL in bear regimes), short trend captures mitigated portfolio drawdown.

---

### Phase 3: Multi-Asset Portfolio Backtester (Closing the Single-Position Gap)

Earlier versions evaluated assets independently. We implemented `src/trading/backtest/portfolio.py`:
* **Unified Account State:** Replays multi-asset historical streams through a single `AccountState` with concurrent positions.
* **Timestamp-Merged Timeline:** Assets are sorted and stepped by strict timestamp (`ts_ms`), never by list index, ensuring realistic multi-asset concurrency.
* **Real-Time Cross-Asset Correlation Guard:** Computes lookback log-return correlation matrices (`log_return_correlation`) online. If correlation $> \text{max\_correlation}$ (e.g., $0.70$) between a proposed signal and open positions, new risk is blocked.
* **Portfolio Heat Enforcement:** Ensures total active risk across all open positions does not exceed `max_heat` ($6.0\%$) or `max_heat_high_vol` ($3.0\%$).

---

### Phase 4: Advanced Statistical & Tail-Risk Engineering

To transform the system into an institutional quant engine, we built five mathematical risk engines:

1. **GARCH(1,1) Dynamic Volatility Sizing (`src/trading/risk/garch.py`):**
   - Fits conditional variance $\sigma_t^2 = \omega + \alpha \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2$ on log-returns via the `arch` library.
   - Computes 1-step ahead annualized volatility $\hat{\sigma}_{\text{ann}}$ to scale position risk percentage ($\text{scale} = \sigma_{\text{target}} / \hat{\sigma}_{\text{ann}}$, bounded $[0.25, 1.50]$) *before* volatility spikes inflict damage.

2. **3-State Gaussian Hidden Markov Model (HMM) (`src/trading/risk/hmm_regime.py`):**
   - Unsupervised regime classifier fitted on joint $[r_t, \text{std}_5(r_t)]$ observation vectors using `hmmlearn`.
   - Classifies market into `trending_bull` (State 0), `volatile_bear` (State 1), and `choppy_sideways` (State 2) with posterior probabilities $P(S_t = k)$ and a full transition matrix.

3. **Extreme Value Theory (EVT) Peaks-Over-Threshold (POT) (`src/trading/risk/evt.py`):**
   - Fits a Generalized Pareto Distribution (GPD) with shape parameter $\xi$ and scale parameter $\beta$ to the upper 10% tail of loss distributions via `scipy.stats.genpareto`.
   - Computes fat-tailed 99% Value-at-Risk ($\text{VaR}_{0.99}$) and 99% Expected Shortfall (Tail-VaR / CVaR). Flags heavy-tail regimes when $\xi > 0.10$.

4. **Vine Copula Multi-Asset Dependency Engine (`src/trading/risk/copula.py`):**
   - Maps asset return marginals to uniform margins via empirical CDFs.
   - Measures non-linear dependency and empirical lower tail co-dependence ($\lambda_L = \lim_{q \to 0} \frac{P(U \le q, V \le q)}{q}$) and upper tail co-dependence ($\lambda_U$).
   - Automatically classifies copula structures (`clayton`, `gumbel`, `student_t`, `gaussian`) and triggers cluster risk ceilings when assets display joint crash contagion ($\lambda_L > 0.60$).

5. **Automaton-Style Survival State Engine (`src/trading/risk/survival.py`):**
   - Modulates account operational tiers based on composite health metrics:
     - `NORMAL` (1.0x risk cap): Standard operation.
     - `CAUTION` (0.5x risk cap): Triggered by drawdown $>10\%$, weekly loss stress, or HMM high-volatility bear regime. Requires higher signal confidence floor ($>0.75$).
     - `SURVIVAL` (0.0x new risk, capital defense only): Triggered by daily loss limit breach or $\ge 3$ consecutive losses.
     - `COOLDOWN` (0.0x risk, system paused): Triggered by maximum allowable drawdown breach ($>17.5\%$) or active kill switch.

---

### Phase 5: Autonomous Heartbeat Daemon & Bayesian Learning Graph

1. **Continuous Heartbeat Loop (`src/trading/daemon/heartbeat.py`):**
   - Implements an asynchronous `Think -> Act -> Observe -> Reflect` operational daemon.
   - Every tick: updates market price buffers $\to$ runs GARCH/HMM/EVT/Copula models $\to$ evaluates Survival Tier $\to$ routes active strategy $\to$ submits signals to `RiskEngine` $\to$ logs telemetry.

2. **Directed Multigraph & Bayesian Learning Graph (`src/trading/learning/graph.py`):**
   - Directed NetworkX multigraph storing `(decision_node) -> (result_node)` edges.
   - Updates conjugate Normal-Normal Bayesian posteriors ($\mu_{\text{post}}, \sigma^2_{\text{post}}$) for trade PnL distributions upon trade completion.
   - Atomic JSON-Lines serialization (`learning_graph.jsonl`) and SQLite indexing bridge (`export_to_sqlite`).

3. **Contextual Bandit Policy Allocator (`src/trading/learning/policy.py`):**
   - Softmax policy gradient (REINFORCE) and Thompson-sampling bandit for routing capital dynamically across candidate strategies based on realized R-multiples and HMM regime contexts.

---

### Phase 6: High-Sample Statistical Benchmarking (10,000+ Trades)

To verify the system's asymptotic stability and prevent sample-size deception, we created high-throughput synthetic benchmark suites (`benchmark_10k_trades.py` and `benchmark_performance.py`):
* Tested $120,000+$ candles per run generating $10,000+$ closed trades.
* Verified that the `RiskEngine` and `SurvivalEngine` mathematically cap maximum drawdowns even during prolonged synthetic bear/chop regimes with high loss clustering.

---

### Phase 7: Real-Time Interactive Web Cockpit & UI Terminal

An interactive, glassmorphic Web Dashboard is served locally via `server.py` (`http://localhost:8080`):
* **Live Candlestick Stream:** Real-time canvas-rendered multi-asset chart.
* **HMM Regime Radar & Posterior Gauges:** Real-time visual display of Bull/Bear/Chop probabilities and transitions.
* **GARCH Volatility & EVT Tail-VaR Gauges:** Visual warnings when conditional volatility or tail-risk spikes.
* **Copula Dependency Matrix:** Real-time asset-pair correlation and crash dependency heatmap.
* **Survival Tier Badge:** Displays dynamic state transitions (`NORMAL`, `CAUTION`, `SURVIVAL`, `COOLDOWN`).
* **Interactive Volatility Shock Simulator:** Button to inject sudden $-15\%$ market shocks to visually demonstrate the survival engine's immediate transition to `SURVIVAL`/`COOLDOWN` tier.

---

## 4. Full Codebase Architecture & File Catalog

```
algo-trading-system/
├── ANTIGRAVITY.md                # System brief & invariant contract
├── SYSTEM_STATE_AND_HANDOFF.md   # [THIS FILE] Master handoff & architectural specification
├── pyproject.toml                # Dependencies: pydantic-settings, asyncpg, redis, ccxt, arch, hmmlearn, copulas, scipy
├── docker-compose.yml            # Local PostgreSQL 16 & Redis 7 stack
├── server.py                     # Embedded HTTP server for Web UI cockpit (port 8080)
├── benchmark_10k_trades.py       # 10,000+ trade statistical validity benchmark suite
├── benchmark_performance.py      # System execution latency & memory benchmark suite
├── learning_graph.jsonl          # Persisted Bayesian trade decision/result graph
│
├── src/trading/
│   ├── config.py                 # Pydantic Settings loading environment variables
│   ├── indicators.py             # atr(), donchian(), log_return_correlation()
│   ├── journal.py                # PostgreSQL trade decision journal logging
│   │
│   ├── risk/
│   │   ├── models.py             # Frozen dataclasses: Signal, Position, RiskConfig, AccountState, ApprovedOrder, ApprovedExit
│   │   ├── engine.py             # RiskEngine: Sovereign deterministic gating & position sizing
│   │   ├── survival.py           # SurvivalEngine: Automaton 4-tier operational state machine
│   │   ├── garch.py              # GARCH(1,1) conditional volatility forecasting & risk scaling
│   │   ├── hmm_regime.py         # 3-State Gaussian HMM regime classifier
│   │   ├── evt.py                # Extreme Value Theory (GPD) 99% Tail-VaR / Expected Shortfall
│   │   └── copula.py             # Vine Copula joint lower tail dependency & cluster cap
│   │
│   ├── strategy/
│   │   ├── crypto.py             # 1h Mean Reversion (Z-score + RSI + regime filter) [Reference only]
│   │   └── trend.py              # 1d Donchian Breakout trend following [Primary Alpha Engine]
│   │
│   ├── backtest/
│   │   ├── engine.py             # Single-asset event-loop backtester (Wilson CI, binomial test, bootstrap)
│   │   ├── portfolio.py          # Multi-asset concurrent portfolio backtester with correlation/heat guards
│   │   └── strategies.py         # Synthetic candle generators & benchmark strategies
│   │
│   ├── learning/
│   │   ├── graph.py              # NetworkX Decision->Result graph with Bayesian Normal-Normal conjugate updates
│   │   └── policy.py             # Contextual Bandit (Policy Gradient REINFORCE / Thompson sampling)
│   │
│   ├── daemon/
│   │   └── heartbeat.py          # Autonomous continuous Think->Act->Observe->Reflect execution loop
│   │
│   ├── data/
│   │   └── crypto.py             # CCXT live/historical data ingestion to PostgreSQL
│   │
│   └── db/
│       ├── postgres.py           # asyncpg connection pooling
│       └── redis.py              # Redis async client
│
├── scripts/
│   ├── portfolio_backtest.py     # Multi-asset 5-token 7-year portfolio backtest execution script
│   ├── compare_strategies.py     # Side-by-side mean-reversion vs trend-following comparative report
│   ├── evaluate_trend.py         # 5-asset 7-year trail-multiplier parameter sweep script
│   ├── trend_attribution.py      # Long vs Short book decomposition script
│   ├── diagnose_trades.py        # Granular trade-by-trade payoff & friction analyzer
│   ├── diagnose_regime.py        # Regime filter threshold & slope distribution analyzer
│   └── diagnose_volatility.py    # Stop-width vs fee friction ratio analyzer
│
├── tests/
│   ├── test_risk_engine.py       # RiskEngine gating, private issuer, sizing invariant tests
│   ├── test_survival.py          # Survival tier state transition tests
│   ├── test_garch.py             # GARCH(1,1) estimation, forecasting & vol-scaling tests
│   ├── test_hmm_regime.py        # HMM regime classification & posterior probability tests
│   ├── test_evt.py               # EVT GPD fitting, VaR/CVaR computation tests
│   ├── test_copula.py            # Copula rank correlation & tail dependency tests
│   ├── test_backtest_portfolio.py# Multi-asset concurrency, timeline merging & correlation guard tests
│   ├── test_backtest_engine.py   # Pessimistic stop-fill, Wilson CI, bootstrap, trailing stop tests
│   ├── test_strategy_trend.py    # Donchian breakout signal generation tests
│   ├── test_strategy_crypto.py   # Mean reversion signal generation tests
│   ├── test_indicators.py        # ATR, Donchian, log_return_correlation tests
│   └── test_heartbeat.py         # Autonomous daemon cycle execution tests
│
└── web/                          # Real-Time Interactive Terminal UI
    ├── index.html                # Dark-mode quantum HUD layout
    ├── style.css                 # Glassmorphic, modern CSS design system
    └── app.js                    # Live simulation stream, charts, HMM radar, EVT gauges
```

---

## 5. Mathematical & Statistical Formulations

### A. Position Sizing
$$\text{Units} = \frac{\text{Equity} \times \text{RiskPct}}{|\text{Entry Price} - \text{Stop Price}|}, \quad \text{where } \text{RiskPct} \le 0.02$$

### B. Derived Breakeven Win Rate
$$\text{WinRate}_{\text{BE}} = \frac{\overline{\text{Loss}_R}}{\overline{\text{Win}_R} + \overline{\text{Loss}_R}}$$

### C. GARCH(1,1) Conditional Volatility Forecast
$$\sigma_t^2 = \omega + \alpha \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2, \quad \hat{\sigma}_{t+1} = \sqrt{\omega + (\alpha + \beta)\sigma_t^2}$$
$$\text{VolScale} = \operatorname{clip}\left(\frac{\sigma_{\text{target}}}{\hat{\sigma}_{t+1} \times \sqrt{365}}, \, 0.25, \, 1.50\right)$$

### D. EVT Peaks-Over-Threshold (POT) Tail-VaR & Expected Shortfall
For loss exceedances $y = L - u > 0$ modeled by GPD $G_{\xi, \beta}(y) = 1 - \left(1 + \frac{\xi y}{\beta}\right)^{-1/\xi}$:
$$\text{VaR}_{1-\alpha} = u + \frac{\beta}{\xi}\left[\left(\frac{N}{N_u} \alpha\right)^{-\xi} - 1\right]$$
$$\text{ES}_{1-\alpha} = \text{CVaR}_{1-\alpha} = \frac{\text{VaR}_{1-\alpha} + \beta - \xi u}{1 - \xi} \quad (\text{for } \xi < 1)$$

### E. Lower Tail Co-Dependence ($\lambda_L$)
$$\lambda_L = \lim_{q \to 0^+} P(U \le q \mid V \le q) = \lim_{q \to 0^+} \frac{C(q, q)}{q}$$

### F. Normal-Normal Bayesian Posterior Conjugate Update
$$\mu_{\text{post}} = \frac{\frac{\mu_0}{\sigma_0^2} + \frac{x_{\text{pnl}}}{\sigma_{\text{obs}}^2}}{\frac{1}{\sigma_0^2} + \frac{1}{\sigma_{\text{obs}}^2}}, \quad \sigma^2_{\text{post}} = \frac{1}{\frac{1}{\sigma_0^2} + \frac{1}{\sigma_{\text{obs}}^2}}$$

---

## 6. Known Gaps, Remaining Deficiencies, & Technical Debt

While the system is mathematically mature and fully tested in backtesting/simulation, the following gaps exist before live capital deployment:

1. **Lack of Live/Paper Execution Layer:**
   - No CCXT exchange order execution bridge.
   - No orderbook websocket stream handler or tick reconciliation.
   - No restart/crash recovery state manager for live in-flight orders.
2. **Deflated Sharpe Ratio (DSR) & Walk-Forward Cross-Validation:**
   - While daily trend following was validated on 70/30 train/test across 5 assets and 5 trail-multipliers, it has not yet run through a formal purged K-fold cross-validation or Bailey & López de Prado's Deflated Sharpe Ratio calculation to mathematically penalize for trial count.
3. **Perpetual Swap Funding Rate Modeling:**
   - Long/Short holding periods over days/weeks currently assume zero funding cost. In prolonged bull runs, short funding yields a positive carry, while long positions pay funding.
4. **Execution Slippage & Market Impact Model:**
   - Slippage is currently modeled as fixed percentage basis points ($0.05\%$). Orderbook depth-dependent market impact (square-root law) is not yet incorporated for larger notional position sizes.
5. **Pairs Trading / Cointegration Backtester Gap:**
   - Cointegration and statistical arbitrage between pairs (e.g. BTC/ETH) were identified as ideal market-neutral complements, but require a 2-leg spread execution engine.

---

## 7. Specific Inquiry Framework for LLM Recommendations

When presenting this document to other LLM models (e.g., Claude 3.7 Sonnet, OpenAI O1/O3, DeepSeek-R1, Gemini Pro), ask them to evaluate the following strategic decisions:

### Question 1: Architecture & Live Deployment Roadmap
> *"Given the current backtest-verified architecture and the strict RiskEngine invariant boundary, what is the cleanest, most failure-proof architecture for implementing the Paper $\to$ Testnet $\to$ Live execution layer using CCXT (async)? Specifically, how should order reconciliation, websocket reconnects, and state synchronization with Postgres/Redis be structured without introducing race conditions?"*

### Question 2: Statistical Rigor & Overfitting Controls
> *"We have verified out-of-sample edge on 1d Donchian trend following across 5 crypto majors over 7 years. What specific statistical verification techniques (e.g. Combinatorial Purged Cross-Validation [CPCV], Deflated Sharpe Ratio, White's Reality Check) should be implemented next to rigorously certify that this edge is statistically significant against data-mining bias?"*

### Question 3: Market-Neutral Complement Strategy Formulation
> *"Trend-following exhibits high positive skew but suffers during extended sideways chop. Given our multi-asset portfolio engine (`portfolio.py`) and Copula dependency framework, what is the best market-neutral strategy to build next (e.g., cross-sectional momentum, funding-rate carry arbitrage, or Johansen-cointegrated pairs trading) to smooth out the portfolio equity curve?"*

### Question 4: Survival Tier Parameter Tuning & Adaptive Sizing
> *"Our Automaton-inspired SurvivalEngine modulates between NORMAL (1.0x), CAUTION (0.5x), SURVIVAL (0.0x), and COOLDOWN. How can we optimize the transition thresholds between these states using reinforcement learning (Contextual Bandit) or control theory without overfitting to historical drawdowns?"*

---
*End of Specification Document.*
