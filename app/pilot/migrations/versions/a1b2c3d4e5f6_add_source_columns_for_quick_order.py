"""add_source_columns_for_quick_order

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-07-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE item_signals
        ADD COLUMN IF NOT EXISTS source VARCHAR NOT NULL DEFAULT 'flow'
    """)
    op.execute("""
        ALTER TABLE orders
        ADD COLUMN IF NOT EXISTS source VARCHAR NOT NULL DEFAULT 'flow'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE item_signals DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS source")
