"""add_flow_tables

Revision ID: f1a2b3c4d5e6
Revises: e3a1f92b7c04
Create Date: 2026-07-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f1a2b3c4d5e6'
down_revision = 'e3a1f92b7c04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS household_models (
            id UUID NOT NULL PRIMARY KEY,
            household_id UUID NOT NULL UNIQUE REFERENCES households(id) ON DELETE CASCADE,
            anchors JSONB DEFAULT '[]',
            preferences JSONB DEFAULT '{}',
            confirmation_behaviour JSONB DEFAULT '{}',
            avg_edit_count FLOAT,
            reorder_horizon_days INTEGER,
            last_evaluated_at TIMESTAMP WITH TIME ZONE,
            last_updated TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS flow_baskets (
            id UUID NOT NULL PRIMARY KEY,
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            loop_run_id UUID REFERENCES loop_runs(id),
            generated_at TIMESTAMP WITH TIME ZONE,
            validated_at TIMESTAMP WITH TIME ZONE,
            delivered_at TIMESTAMP WITH TIME ZONE,
            generated_items JSONB DEFAULT '[]',
            validated_items JSONB DEFAULT '[]',
            dropped_items JSONB DEFAULT '[]',
            status VARCHAR NOT NULL DEFAULT 'held'
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS item_signals (
            id UUID NOT NULL PRIMARY KEY,
            household_id UUID NOT NULL REFERENCES households(id) ON DELETE CASCADE,
            loop_run_id UUID REFERENCES loop_runs(id),
            item_name VARCHAR NOT NULL,
            signal_type VARCHAR NOT NULL,
            previous_value JSONB,
            new_value JSONB,
            recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS item_signals")
    op.execute("DROP TABLE IF EXISTS flow_baskets")
    op.execute("DROP TABLE IF EXISTS household_models")
