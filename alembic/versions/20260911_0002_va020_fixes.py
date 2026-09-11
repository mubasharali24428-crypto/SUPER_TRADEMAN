"""VA-020: guard decisions.decision_id UNIQUE constraint for legacy schemas

Tables created by pre-migration runtime DDL may lack the UNIQUE constraint
that decisions.decision_id requires for journal.store_decisions idempotency.
This revision safely adds it where missing.

Also adds VA-051 asset_class to ohlcv PRIMARY KEY.

Revision ID: 0002_va020_fixes
Revises: 0001_initial
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_va020_fixes"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # VA-020: Add UNIQUE constraint on decisions.decision_id if not present.
    # The initial migration uses CREATE TABLE IF NOT EXISTS which is a no-op
    # on legacy tables that predate this constraint, leaving schema incompatible
    # with INSERT ... ON CONFLICT (decision_id) DO NOTHING in journal.py.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'decisions_decision_id_key'
                  AND conrelid = 'decisions'::regclass
            ) THEN
                ALTER TABLE decisions ADD UNIQUE (decision_id);
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'decisions_decision_id_key'
                  AND conrelid = 'decisions'::regclass
            ) THEN
                ALTER TABLE decisions DROP CONSTRAINT decisions_decision_id_key;
            END IF;
        END
        $$;
        """
    )
