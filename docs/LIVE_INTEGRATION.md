# LIVE_INTEGRATION.md

Real venue connectivity for the trading system, delivered by **P3-ALPHA**
(wave 6, Phase 3). Everything here sits behind the existing `VenueAdapter`
protocol and the `ExecutionMode` hierarchy — simulation code paths are
unchanged.

## Components

| Module | Purpose |
| --- | --- |
| `src/trading/execution/live_adapter.py` | `CcxtLiveAdapter` — full `VenueAdapter` ABC implementation over ccxt |
| `src/trading/data/pipeline.py` | `fetch_and_store_ohlcv` — paginated OHLCV ingestion into Postgres with gap detection |
| `src/trading/data/websocket_feed.py` | `watch_ohlcv_loop` + `HeartbeatBuffer` — streaming candles with reconnect/backoff |
| `src/trading/config.py` | Live-venue settings (`venue_api_key`, `venue_api_secret`, `venue_password`, `exchange_id`, `dry_run`) |

## Setup (testnet credentials via env only)

No credentials ever live in the repository. Export testnet keys into the
environment (or your `.env`, which pydantic-settings loads):

```bash
export POSTGRES_URL="postgresql://user:pass@localhost:5432/trading"
export REDIS_URL="redis://localhost:6379/0"

# Venue credentials (Binance.US testnet shown; any ccxt exchange id works)
export VENUE_API_KEY="..."          # -> Settings.venue_api_key
export VENUE_API_SECRET="..."       # -> Settings.venue_api_secret
export VENUE_PASSWORD="..."         # optional; some venues need a passphrase
export EXCHANGE_ID="binanceus"      # default
export EXECUTION_MODE="live_restricted"   # or live_full
export DRY_RUN="true"               # start here — see gating table below
```

Apply the DB schema before ingesting (runtime never issues DDL):

```bash
POSTGRES_URL=$POSTGRES_URL .venv/bin/alembic upgrade head
```

Minimal usage:

```python
from trading.execution.live_adapter import CcxtLiveAdapter
adapter = CcxtLiveAdapter()            # reads Settings from env
info = await adapter.get_instrument_info("BTC/USDT")
resp = await adapter.create_order(approved_order, client_order_id)
```

## Safety rails

1. **Approved orders only.** Submissions must arrive as `ApprovedOrder`
   (entries) / `ApprovedExit` (exits) minted by the Risk Engine. The adapter
   asserts the exact type and raises `OrderRejectedError` otherwise; it never
   constructs orders itself.
2. **Mode gating.** Construction refuses any mode outside
   `{LIVE_RESTRICTED, LIVE_FULL}` unless `dry_run=True`. Live (non-dry-run)
   construction additionally requires `VENUE_API_KEY` + `VENUE_API_SECRET`.
3. **Dry-run paper logging.** With `dry_run=True` every would-be submission is
   logged (`[DRY-RUN SUBMISSION] ...`) and recorded on `adapter.calls`; no
   exchange object is ever built — the path is fully offline.
4. **Kill switch honored pre-submit.** A tripped `AccountState.kill_switch`
   raises `KillSwitchActiveError` before any network traffic.
5. **Order-size clamp.** Quantity/price pass through
   `trading.core.money.quantize_to_step` using limits from
   `get_instrument_info` (ccxt markets), rounding DOWN onto the venue grid;
   min-notional is re-checked after quantization.
6. **Timeout everywhere.** Every venue call runs under
   `asyncio.wait_for(..., call_timeout_s)` (default 10 s).
7. **Retry policy.** Only NetworkError-class failures retry (3 retries,
   exponential backoff). `RateLimitExceeded` — which subclasses `NetworkError`
   in ccxt ≥4 — is checked first and raised to the caller untouched.
8. **State-machine discipline.** ccxt exceptions map to OrderStates via
   `classify_ccxt_error` / `next_state_on_venue_error`, routed through the
   existing `LEGAL_TRANSITIONS` table (unknown outcomes quarantine).
9. **Secrets via env only.** No credential literals anywhere in source.

## Mode gating table

| ExecutionMode | dry_run=True | dry_run=False |
| --- | --- | --- |
| BACKTEST | construct OK · logs only | **refused** (`LiveModeError`) |
| PAPER | construct OK · logs only | **refused** |
| SHADOW | construct OK · logs only | **refused** |
| LIVE_RESTRICTED | construct OK · logs only | real submissions (creds required) |
| LIVE_FULL | construct OK · logs only | real submissions (creds required) |

Data-plane components (`pipeline.py`, `websocket_feed.py`) are read-only and
legal in every mode.

## Known limitations

- `create_exit` submits a reduce-only market order against venue-held balance;
  `ApprovedExit` carries no quantity/price by design, so exact-quantity exits
  require a future extension (keyword-only params) without breaking the ABC.
- Gap detection covers candle-cadence holes plus an injectable maintenance
  calendar; exchange-specific halts beyond that calendar still log as gaps.
- Retry/backoff constants (`_MAX_RETRIES=3`, base 0.25 s) are module-level;
  per-call tuning requires code change, not config.
- `websocket_feed.py` uses REST polling fallback when the exchange object has
  no `watch_ohlcv` (ccxt sync builds); true WebSocket streams need a
  ccxt.pro-style async client instance injected at construction.
- Kill-switch checks use the `AccountState` reference handed to the adapter;
  callers must keep it fresh for the rail to reflect live risk state.
- Testnet coverage varies by venue; Binance.US production endpoints may not
  offer a public testnet — verify per-exchange before enabling LIVE modes.

## Verification

```bash
.venv/bin/python -m pytest tests/trading/execution/test_live_adapter.py \
                           tests/trading/data/test_pipeline.py \
                           tests/trading/data/test_websocket_feed.py -q
```

All tests run against fake ccxt clients — no network access anywhere.
