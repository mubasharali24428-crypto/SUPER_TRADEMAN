"""VA-051: add asset_class to ohlcv/funding_rates PRIMARY KEY

The initial migration (0001) defines PRIMARY KEY (exchange, symbol, timeframe, timestamp)
for ohlcv and (exchange, symbol, timestamp) for funding_rates, but omits asset_class
from the key. This means the same exchange+symbol+timeframe at the same ts with
different asset_class values would collide — or silently overwrite.

This migration drops the old PK and recreates it with asset_class included.
If duplicate rows exist under the narrower key the migration raises an error.

Revision ID: 0003_va051_fixes
Revises: 0002_va020_fixes
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_va051_fixes"
down_revision: Union[str, None] = "0002_va020_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK_DUPLICATES = """
SELECT COUNT(*) FROM ohlcv o
WHERE EXISTS (
    SELECT 1 FROM ohlcv o2
    WHERE o2.exchange = o.exchange
      AND o2.symbol = o.symbol
      AND o2.timeframe = o.timeframe
      AND o2.timestamp = o.timestamp
      AND o2.asset_class IS DISTINCT FROM o.asset_class
)
"""


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(text("SELECT COUNT(*) FROM ohlcv")).scalar()
    if result and result > 0:
        dup_count = conn.execute(text(_CHECK_DUPLICATES)).scalar()
        if dup_count and dup_count > 0:
            raise RuntimeError(
                f"VA-051: {dup_count} ohlcv rows share exchange+symbol+timeframe+timestamp "
                f"but differ in asset_class. Deduplicate before upgrading."
            )
    op.execute("ALTER TABLE ohlcv DROP CONSTRAINT ohlcv_pkey;")
    op.execute(
        "ALTER TABLE ohlcv ADD PRIMARY KEY (exchange, symbol, timeframe, timestamp, asset_class);"
    )
    op.execute("ALTER TABLE funding_rates DROP CONSTRAINT funding_rates_pkey;")
    op.execute(
        "ALTER TABLE funding_rates ADD PRIMARY KEY (exchange, symbol, timestamp, asset_class);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE ohlcv DROP CONSTRAINT ohlcv_pkey;")
    op.execute(
        "ALTER TABLE ohlcv ADD PRIMARY KEY (exchange, symbol, timeframe, timestamp);"
    )
    op.execute("ALTER TABLE funding_rates DROP CONSTRAINT funding_rates_pkey;")
    op.execute(
        "ALTER TABLE funding_rates ADD PRIMARY KEY (exchange, symbol, timestamp);"
    )
