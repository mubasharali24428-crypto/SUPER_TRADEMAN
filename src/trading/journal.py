"""Trade decision journal: persistence of DecisionRecord rows.

Schema ownership: the ``decisions`` table is created by Alembic migrations
(alembic/versions/0001_initial), NOT at runtime. The migration adds a
``decision_id TEXT NOT NULL UNIQUE`` idempotency key so replaying the same
trade (same asset/entry_time/side) updates rather than duplicates.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import asyncpg

from trading.risk.models import Signal


class JournalPool(Protocol):
    """Structural subset of asyncpg.Pool used by the journal (test-friendly)."""

    async def execute(self, sql: str, *args: Any) -> Any: ...

    async def executemany(self, sql: str, rows: Any) -> Any: ...

__all__ = ["DecisionRecord", "build_decision_record", "decision_id_for", "store_decisions"]


@dataclass(frozen=True)
class DecisionRecord:
    """One row of the trade decision journal: two pairs -- (decision, expected
    outcome) made at entry, and (consequence, verdict) observed at exit -- so
    realized results can be reviewed against what was actually expected."""

    asset: str
    entry_time: datetime
    side: str
    decision: str  # the strategy's rationale for the entry
    expected_reward_risk: float
    consequence_r_multiple: float
    consequence_net_pnl: float
    exit_reason: str
    verdict: str


def _verdict(exit_reason: str) -> str:
    return {
        "target": "thesis_confirmed",
        "stop": "thesis_invalidated",
        "time_stop": "inconclusive_ran_out_of_time",
    }.get(exit_reason, "inconclusive_data_ended")  # exit_reason == "end_of_data"


def build_decision_record(signal: Signal, trade) -> DecisionRecord:
    risk_per_unit = abs(signal.entry_price - signal.suggested_stop)
    reward_per_unit = abs(signal.suggested_target - signal.entry_price)
    return DecisionRecord(
        asset=trade.asset,
        entry_time=trade.entry_time,
        side=signal.side.value,
        decision=signal.rationale,
        expected_reward_risk=reward_per_unit / risk_per_unit if risk_per_unit else 0.0,
        consequence_r_multiple=trade.r_multiple,
        consequence_net_pnl=trade.net_pnl,
        exit_reason=trade.exit_reason,
        verdict=_verdict(trade.exit_reason),
    )


def decision_id_for(record: DecisionRecord) -> str:
    """Deterministic identity for one decision row.

    A trade is identified by (asset, entry_time, side): replaying backtests or
    re-running the journal for the same session must UPDATE in place, never
    duplicate. SHA-256 keeps the key stable and collision-free.
    """
    # VA-040: fold the strategy rationale (decision) into the hash so two
    # strategies taking the same asset/side in the same minute no longer
    # collide onto one journal row.
    raw = (
        f"{record.asset}|{record.entry_time.isoformat()}|{record.side}"
        f"|{record.decision}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def store_decisions(pool: "JournalPool", records: list[DecisionRecord]):
    """Persist decision records idempotently.

    INSERT .. ON CONFLICT (decision_id) DO UPDATE: re-storing the same trade
    refreshes consequences/verdict instead of raising on the unique key.
    """
    rows = [
        (
            decision_id_for(r),
            r.asset,
            r.entry_time,
            r.side,
            r.decision,
            r.expected_reward_risk,
            r.consequence_r_multiple,
            r.consequence_net_pnl,
            r.exit_reason,
            r.verdict,
        )
        for r in records
    ]
    await pool.executemany(
        """
        INSERT INTO decisions (decision_id, asset, entry_time, side, decision,
                               expected_reward_risk, consequence_r_multiple,
                               consequence_net_pnl, exit_reason, verdict)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (decision_id) DO UPDATE SET
            decision = EXCLUDED.decision,
            expected_reward_risk = EXCLUDED.expected_reward_risk,
            consequence_r_multiple = EXCLUDED.consequence_r_multiple,
            consequence_net_pnl = EXCLUDED.consequence_net_pnl,
            exit_reason = EXCLUDED.exit_reason,
            verdict = EXCLUDED.verdict
        """,
        rows,
    )
