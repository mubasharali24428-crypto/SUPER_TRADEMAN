from dataclasses import dataclass
from datetime import datetime

import asyncpg

from trading.risk.models import Signal


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


async def ensure_schema(pool: asyncpg.Pool):
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id BIGSERIAL PRIMARY KEY,
            asset TEXT NOT NULL,
            entry_time TIMESTAMPTZ NOT NULL,
            side TEXT NOT NULL,
            decision TEXT NOT NULL,
            expected_reward_risk DOUBLE PRECISION NOT NULL,
            consequence_r_multiple DOUBLE PRECISION NOT NULL,
            consequence_net_pnl DOUBLE PRECISION NOT NULL,
            exit_reason TEXT NOT NULL,
            verdict TEXT NOT NULL
        )
        """
    )


async def store_decisions(pool: asyncpg.Pool, records: list[DecisionRecord]):
    rows = [
        (
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
        INSERT INTO decisions (asset, entry_time, side, decision, expected_reward_risk,
                                consequence_r_multiple, consequence_net_pnl, exit_reason, verdict)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        rows,
    )
