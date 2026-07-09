"""add_routines_tables

Revision ID: e3a1f92b7c04
Revises: d728c68a8d3a
Create Date: 2026-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e3a1f92b7c04'
down_revision = 'd728c68a8d3a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'routines',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('household_id', postgresql.UUID(as_uuid=False),
                  sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('frequency_type', sa.String(), nullable=False),
        sa.Column('frequency_value', sa.Integer(), nullable=False),
        sa.Column('schedule_time', sa.Time(timezone=True), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_days_paused', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_routines_household_status', 'routines', ['household_id', 'status'])
    op.create_index('idx_routines_next_run_at', 'routines', ['next_run_at'])

    op.create_table(
        'routine_items',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('routine_id', postgresql.UUID(as_uuid=False),
                  sa.ForeignKey('routines.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('item_name', sa.String(), nullable=False),
        sa.Column('quantity', sa.Numeric(8, 3), nullable=False, server_default='1'),
        sa.Column('unit', sa.String(), nullable=False, server_default='unit'),
        sa.Column('swiggy_product_id', sa.String(), nullable=True),
        sa.Column('swiggy_product_name', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'routine_runs',
        sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('routine_id', postgresql.UUID(as_uuid=False),
                  sa.ForeignKey('routines.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('order_id', postgresql.UUID(as_uuid=False),
                  sa.ForeignKey('orders.id'), nullable=True),
        sa.Column('skipped_items', sa.Text(), nullable=True),
        sa.Column('skip_reason', sa.String(), nullable=True),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_amount', sa.Numeric(10, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_routine_runs_routine_id', 'routine_runs', ['routine_id'])


def downgrade() -> None:
    op.drop_table('routine_runs')
    op.drop_table('routine_items')
    op.drop_table('routines')
