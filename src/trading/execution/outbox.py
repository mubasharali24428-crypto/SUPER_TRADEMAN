"""Outbox pattern for idempotent order intent persistence."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

import asyncpg

from trading.execution.state_machine import OrderState

__all__ = ["OrderIntent", "generate_client_order_id", "OutboxStore"]


def generate_client_order_id(strategy_id: str, signal_id: str) -> str:
    """Generates a deterministic, unique client order ID."""
    uid = str(uuid.uuid4())[:8]
    return f"{strategy_id}:{signal_id}:{uid}"


@dataclass
class OrderIntent:
    client_order_id: str
    strategy_id: str
    signal_id: str
    asset: str
    side: str
    price: float
    stop_price: float
    quantity: float
    created_at: datetime
    status: OrderState = OrderState.CREATED
    exchange_order_id: Optional[str] = None


class OutboxStore:
    """Outbox persistence backed by PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def ensure_schema(self) -> None:
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS order_intent (
                client_order_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                stop_price DOUBLE PRECISION NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL,
                exchange_order_id TEXT
            );
            """
        )

    async def save_intent(self, intent: OrderIntent) -> None:
        await self.pool.execute(
            """
            INSERT INTO order_intent (
                client_order_id, strategy_id, signal_id, asset, side,
                price, stop_price, quantity, created_at, status, exchange_order_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (client_order_id) DO NOTHING
            """,
            intent.client_order_id,
            intent.strategy_id,
            intent.signal_id,
            intent.asset,
            intent.side,
            intent.price,
            intent.stop_price,
            intent.quantity,
            intent.created_at,
            intent.status.value,
            intent.exchange_order_id,
        )

    async def get_pending_intents(self) -> List[OrderIntent]:
        rows = await self.pool.fetch(
            "SELECT * FROM order_intent WHERE status IN ('created', 'submitted')"
        )
        return [
            OrderIntent(
                client_order_id=r["client_order_id"],
                strategy_id=r["strategy_id"],
                signal_id=r["signal_id"],
                asset=r["asset"],
                side=r["side"],
                price=r["price"],
                stop_price=r["stop_price"],
                quantity=r["quantity"],
                created_at=r["created_at"],
                status=OrderState(r["status"]),
                exchange_order_id=r["exchange_order_id"],
            )
            for r in rows
        ]

    async def update_status(self, client_order_id: str, status: OrderState, exchange_order_id: Optional[str] = None) -> None:
        await self.pool.execute(
            """
            UPDATE order_intent
            SET status = $1, exchange_order_id = COALESCE($2, exchange_order_id)
            WHERE client_order_id = $3
            """,
            status.value,
            exchange_order_id,
            client_order_id,
        )
