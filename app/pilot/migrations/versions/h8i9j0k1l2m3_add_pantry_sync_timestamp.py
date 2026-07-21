"""add_pantry_sync_timestamp

Revision ID: h8i9j0k1l2m3
Revises: g2h3i4j5k6l7
Create Date: 2026-07-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "h8i9j0k1l2m3"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "household_preferences",
        sa.Column("last_external_sync_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("household_preferences", "last_external_sync_at")
