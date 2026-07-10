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
    # household_models
    op.create_table(
        'household_models',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('household_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('households.id', ondelete='CASCADE'), unique=True, nullable=False),
        sa.Column('anchors', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('preferences', postgresql.JSONB(), nullable=True, server_default='{}'),
        sa.Column('confirmation_behaviour', postgresql.JSONB(), nullable=True, server_default='{}'),
        sa.Column('avg_edit_count', sa.Float(), nullable=True),
        sa.Column('reorder_horizon_days', sa.Integer(), nullable=True),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_updated', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # flow_baskets
    op.create_table(
        'flow_baskets',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('household_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=False),
        sa.Column('loop_run_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('loop_runs.id'), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_items', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('validated_items', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('dropped_items', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('status', sa.String(), nullable=False, server_default='held'),
    )

    # item_signals
    op.create_table(
        'item_signals',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('household_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=False),
        sa.Column('loop_run_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('loop_runs.id'), nullable=True),
        sa.Column('item_name', sa.String(), nullable=False),
        sa.Column('signal_type', sa.String(), nullable=False),
        sa.Column('previous_value', postgresql.JSONB(), nullable=True),
        sa.Column('new_value', postgresql.JSONB(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('item_signals')
    op.drop_table('flow_baskets')
    op.drop_table('household_models')
