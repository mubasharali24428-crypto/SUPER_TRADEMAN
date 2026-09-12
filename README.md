# SUPER_TRADEMAN

A crypto algorithmic trading system built around a deterministic risk engine, with a synthetic
adversarial market layer for stress-testing strategy behaviour under liquidity attacks.

**Status: backtest and simulation only.** There is no live execution, no broker connection, and no
money at risk. `config/deployment_config.yml` defines paper, shadow, and restricted-live modes, but
nothing runs against a real venue.

```bash
# compose reads POSTGRES_USER/POSTGRES_PASSWORD from your shell env (NOT .env):
export POSTGRES_USER=stm_dev POSTGRES_PASSWORD=$(openssl rand -base64 24) POSTGRES_DB=super_trademan
docker compose up -d   # Postgres 16 + Redis 7
uv sync
cp .env.example .env
# then set POSTGRES_URL/REDIS_URL in .env to match the credentials above
uv run pytest          # 839 passed, 7 skipped (live-DB tests skip without a reachable Postgres)
```

Live-DB tests require a reachable Postgres with credentials matching `.env`; they skip
(fail-closed) otherwise. CI runs the full suite without Docker — those tests skip there too.

## Design

The risk engine is the only component permitted to open risk, and that is enforced structurally
rather than by convention. `ApprovedOrder` and `ApprovedExit` can only be constructed by code
holding `_ISSUER`, a module-private sentinel in `risk/models.py`; construction from anywhere else
raises `PermissionError`. Position size is always derived from `equity * risk_pct / risk_per_unit`,
never chosen, and `risk_pct` is hard-capped at 2% in `__post_init__`.

Any sentiment, anomaly, or LLM component is confined to a single channel: it may propose closing a
position via `ExitSignal`, never opening or resizing one. `ExitSignal` carries no `entry_price`,
`side`, `stop`, `target`, or size field, so the restriction holds regardless of whether the
component's judgment is correct. No such component exists yet. The constraint was written first.

## Layout

| Path | Contents |
|---|---|
| `risk/` | Risk engine, GARCH(1,1) volatility sizing, 3-state HMM regime classifier, EVT tail-VaR, copula tail-dependence guard, survival state tiers, circuit breaker |
| `synthetic/` | Adversarial market ecology, chaos injection, stale-quote protection, strategy defense state machine, limit order book, venue and OMS engines |
| `stats/` | Probability of backtest overfitting, combinatorial purged cross-validation, effective trials |
| `execution/` | OMS, reconciler, order state machine, shadow mode, transaction cost analysis |
| `strategy/` | Mean reversion and Donchian trend following |
| `backtest/` | Event-loop backtester with slippage, commission, trailing ATR stop, bootstrap and Wilson CI |
| `ops/`, `observability/`, `infrastructure/`, `security/` | Alerting, metrics, health checks, HA locking, state recovery, audit ledger |

## Results

Daily-timeframe Donchian breakout across BTC, ETH, BNB, SOL and XRP over 7 years, 70/30 split, was
profitable at every one of five trailing-stop settings both in and out of sample.

The check that matters is long/short attribution, because three of the five assets returned between
+400% and +5000% buy-and-hold over the window, so an all-long system could look profitable while
only tracking market beta. Out of sample, both books were profitable independently:

```
LONG   n=31   win 41.9%   avgR +0.667   totalR +20.7
SHORT  n=21   win 47.6%   avgR +0.481   totalR +10.1
```

SOL/USDT fell 57.7% across its test window and the strategy lost 5.50% on it rather than blowing up.

The earlier mean-reversion strategy lost money and kept losing it after four separate diagnosed
defects were fixed. It stays in the repository as a tested negative reference rather than being
deleted.

## Known gaps

- The headline result is one uncorrected five-point parameter sweep against a single 70/30 split,
  not a walk-forward study. `stats/pbo.py` and `stats/cross_validation.py` exist to address this
  and have not yet been applied to it.
- No funding-rate modelling for perpetual shorts held over days.
- No gap or liquidity modelling; stops assume a fill at the stop price plus fixed slippage.
- No live execution layer: no order placement, reconciliation against a venue, or restart semantics.

## Risk Disclaimer

Trading cryptocurrency assets involves substantial risk of loss and is not suitable for every
investor. The volatility of crypto markets can cause rapid and total loss of invested capital.
Past performance — including any backtest, simulation, or shadow-mode result presented in this
repository — is not indicative of future results.

Nothing in this repository, its documentation, or its outputs constitutes financial, investment,
or trading advice, nor a recommendation to buy, sell, or hold any asset or instrument.

This software is provided "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. In no event shall the authors or copyright holders be liable for any claim,
damages, or other liability arising from, out of, or in connection with the software or its
use. Any decision to use this software for live trading is made entirely at your own risk. See
[LICENSE](LICENSE) for the full license terms.
