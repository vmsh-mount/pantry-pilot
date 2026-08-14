"""assistant_messages

AI ordering assistant's conversation history. Pure schema — no behavior
change. See tasks/features/ai-ordering-assistant.md, Design §1.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-08-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_messages",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id", UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),

        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tool_calls", JSONB, nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_assistant_messages_household", "assistant_messages",
        ["household_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_assistant_messages_household", table_name="assistant_messages")
    op.drop_table("assistant_messages")
