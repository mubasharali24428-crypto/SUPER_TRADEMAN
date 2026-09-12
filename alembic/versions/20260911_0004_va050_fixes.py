"""VA-050: partial index on order_intent(status) for pending scans.

Without an index, queries filtering by status IN ('created','submitted')
perform sequential scans that degrade linearly with table growth.

Revision ID: 0004_va050_fixes
Revises: 0003_va051_fixes
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0004_va050_fixes"
down_revision: Union[str, None] = "0003_va051_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_order_intent_pending "
        "ON order_intent (status) "
        "WHERE status IN ('created', 'submitted')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_order_intent_pending")
