"""Initial schema — all 13 tables

Revision ID: 0001
Revises:
Create Date: 2026-06-26 00:00:00.000000

Creates:
  households, household_preferences, household_members,
  swiggy_tokens, addresses, brand_preferences,
  pantry_items, loop_runs, loop_run_items, loop_run_edits,
  orders, order_items
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision:       str                        = "0001"
down_revision:  Union[str, None]           = None
branch_labels:  Union[str, Sequence[str], None] = None
depends_on:     Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # ── households ────────────────────────────────────────────────────────────
    op.create_table(
        "households",
        sa.Column("id",                  postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("swiggy_user_id",      sa.String(),  nullable=False, unique=True),
        sa.Column("whatsapp_number",     sa.String(),  nullable=True),
        sa.Column("whatsapp_verified",   sa.Boolean(), server_default="false", nullable=False),
        sa.Column("whatsapp_opted_out",  sa.Boolean(), server_default="false", nullable=False),
        sa.Column("household_type",      sa.String(),  nullable=False),
        sa.Column("member_count",        sa.Integer(), server_default="1", nullable=False),
        sa.Column("diet_type",           sa.String(),  nullable=False),
        sa.Column("allergies",           postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("weekly_budget_min",   sa.Integer(), nullable=True),
        sa.Column("weekly_budget_max",   sa.Integer(), nullable=True),
        sa.Column("onboarding_complete", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active",           sa.Boolean(), server_default="true",  nullable=False),
        sa.Column("is_paused",           sa.Boolean(), server_default="false", nullable=False),
        sa.Column("paused_at",           sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_reason",       sa.String(),  nullable=True),
        sa.Column("city",                sa.String(),  server_default="Bengaluru", nullable=False),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── household_preferences ─────────────────────────────────────────────────
    op.create_table(
        "household_preferences",
        sa.Column("id",                       postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",             postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("preferred_order_day",      sa.String(), server_default="sunday",  nullable=False),
        sa.Column("preferred_order_time",     sa.Time(),   server_default="10:00:00", nullable=False),
        sa.Column("freq_staples",             sa.String(), server_default="weekly",  nullable=False),
        sa.Column("freq_fresh_produce",       sa.String(), server_default="weekly",  nullable=False),
        sa.Column("freq_dairy_eggs",          sa.String(), server_default="weekly",  nullable=False),
        sa.Column("freq_packaged",            sa.String(), server_default="weekly",  nullable=False),
        sa.Column("preferred_address_id",     postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("preferred_delivery_slot",  sa.String(), server_default="evening", nullable=False),
        sa.Column("confirmation_window_hrs",  sa.Integer(), server_default="4",      nullable=False),
        sa.Column("next_run_at",              sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at",              sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",              sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",              sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_preferences_next_run", "household_preferences", ["next_run_at"])

    # ── household_members ─────────────────────────────────────────────────────
    op.create_table(
        "household_members",
        sa.Column("id",             postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",   postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role",           sa.String(), nullable=True),
        sa.Column("diet_override",  sa.String(), nullable=True),
        sa.Column("age_years",      sa.Integer(), nullable=True),
        sa.Column("sex",            sa.String(),  nullable=True),
        sa.Column("weight_kg",      sa.Numeric(5, 1), nullable=True),
        sa.Column("height_cm",      sa.Numeric(5, 1), nullable=True),
        sa.Column("activity_level", sa.String(),  nullable=True),
        sa.Column("health_flags",   postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",     sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── swiggy_tokens ─────────────────────────────────────────────────────────
    op.create_table(
        "swiggy_tokens",
        sa.Column("id",                  postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",        postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("access_token_enc",    sa.Text(), nullable=False),
        sa.Column("token_expiry",        sa.DateTime(timezone=True), nullable=False),
        sa.Column("nudge_48hr_sent",     sa.Boolean(), server_default="false", nullable=False),
        sa.Column("nudge_24hr_sent",     sa.Boolean(), server_default="false", nullable=False),
        sa.Column("nudge_expired_sent",  sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at",        sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_tokens_expiry", "swiggy_tokens", ["token_expiry"])

    # ── addresses ─────────────────────────────────────────────────────────────
    op.create_table(
        "addresses",
        sa.Column("id",                 postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",       postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("swiggy_address_id",  sa.String(), nullable=False),
        sa.Column("label",              sa.String(), nullable=True),
        sa.Column("area",               sa.String(), nullable=True),
        sa.Column("city",               sa.String(), server_default="Bengaluru", nullable=False),
        sa.Column("is_default",         sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at",         sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("household_id", "swiggy_address_id", name="uq_addresses_household_swiggy"),
    )

    # Add FK from household_preferences.preferred_address_id → addresses.id
    op.create_foreign_key(
        "fk_prefs_preferred_address",
        "household_preferences", "addresses",
        ["preferred_address_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── brand_preferences ─────────────────────────────────────────────────────
    op.create_table(
        "brand_preferences",
        sa.Column("id",              postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",    postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category",        sa.String(), nullable=False),
        sa.Column("item_name",       sa.String(), nullable=False),
        sa.Column("preferred_brand", sa.String(), nullable=False),
        sa.Column("confidence",      sa.Numeric(3, 2), nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("household_id", "item_name", name="uq_brand_prefs_household_item"),
    )

    # ── pantry_items ──────────────────────────────────────────────────────────
    op.create_table(
        "pantry_items",
        sa.Column("id",                       postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",             postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_name",                sa.String(), nullable=False),
        sa.Column("category",                 sa.String(), nullable=False),
        sa.Column("standard_unit",            sa.String(), nullable=False),
        sa.Column("last_ordered_qty",         sa.Numeric(8, 3), nullable=True),
        sa.Column("last_ordered_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_qty_remaining",  sa.Numeric(8, 3), server_default="0", nullable=False),
        sa.Column("reorder_threshold",        sa.Numeric(8, 3), nullable=False),
        sa.Column("avg_weekly_consumption",   sa.Numeric(8, 3), nullable=True),
        sa.Column("consumption_confidence",   sa.Numeric(3, 2), server_default="0.0", nullable=False),
        sa.Column("times_ordered",            sa.Integer(), server_default="0", nullable=False),
        sa.Column("times_removed_by_user",    sa.Integer(), server_default="0", nullable=False),
        sa.Column("times_kept_by_user",       sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_user_action",         sa.String(), nullable=True),
        sa.Column("last_user_action_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active",                sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at",               sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",               sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("household_id", "item_name", name="uq_pantry_household_item"),
    )
    op.create_index("idx_pantry_reorder",  "pantry_items", ["household_id", "estimated_qty_remaining", "reorder_threshold"])
    op.create_index("idx_pantry_category", "pantry_items", ["household_id", "category"])

    # ── orders (created before loop_runs because loop_runs FKs to it) ─────────
    op.create_table(
        "orders",
        sa.Column("id",                 postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",       postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("loop_run_id",        postgresql.UUID(as_uuid=False), nullable=True),   # FK added after loop_runs
        sa.Column("swiggy_order_id",    sa.String(), unique=True, nullable=False),
        sa.Column("swiggy_address_id",  sa.String(), nullable=False),
        sa.Column("delivery_slot",      sa.String(), nullable=True),
        sa.Column("item_total",         sa.Numeric(10, 2), nullable=False),
        sa.Column("delivery_fee",       sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("taxes",              sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("grand_total",        sa.Numeric(10, 2), nullable=False),
        sa.Column("status",             sa.String(), server_default="placed", nullable=False),
        sa.Column("placed_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("pantry_updated",     sa.Boolean(), server_default="false", nullable=False),
        sa.Column("pantry_updated_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",         sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",         sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_orders_pantry_update", "orders", ["pantry_updated", "placed_at"])

    # ── loop_runs ─────────────────────────────────────────────────────────────
    op.create_table(
        "loop_runs",
        sa.Column("id",                    postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id",          postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("triggered_at",          sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("trigger_type",          sa.String(), server_default="scheduled", nullable=False),
        sa.Column("state",                 sa.String(), server_default="pending", nullable=False),
        sa.Column("sense_started_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("sense_completed_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_started_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_completed_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("optimize_started_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("optimize_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_sent_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirm_responded_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("place_started_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("place_completed_at",    sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_action",           sa.String(), nullable=True),
        sa.Column("time_to_respond_sec",   sa.Integer(), nullable=True),
        sa.Column("order_id",              postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("skip_reason",           sa.String(), nullable=True),
        sa.Column("failure_reason",        sa.String(), nullable=True),
        sa.Column("failure_stage",         sa.String(), nullable=True),
        sa.Column("llm_model",             sa.String(), nullable=True),
        sa.Column("llm_tokens_used",       sa.Integer(), nullable=True),
        sa.Column("llm_latency_ms",        sa.Integer(), nullable=True),
        sa.Column("substitutions_count",   sa.Integer(), server_default="0", nullable=False),
        sa.Column("items_unavailable",     sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at",            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",            sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_loop_runs_household", "loop_runs", ["household_id"])
    op.create_index("idx_loop_runs_state",     "loop_runs", ["state"])

    # Back-fill the loop_run_id FK on orders now that loop_runs exists
    op.create_foreign_key(
        "fk_orders_loop_run",
        "orders", "loop_runs",
        ["loop_run_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── loop_run_items ────────────────────────────────────────────────────────
    op.create_table(
        "loop_run_items",
        sa.Column("id",                   postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("loop_run_id",          postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("loop_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id",         postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("item_name",            sa.String(), nullable=False),
        sa.Column("swiggy_sku_id",        sa.String(), nullable=True),
        sa.Column("swiggy_product_name",  sa.String(), nullable=True),
        sa.Column("brand",                sa.String(), nullable=True),
        sa.Column("quantity",             sa.Numeric(8, 3), nullable=False),
        sa.Column("unit",                 sa.String(), nullable=False),
        sa.Column("unit_price",           sa.Numeric(10, 2), nullable=True),
        sa.Column("total_price",          sa.Numeric(10, 2), nullable=True),
        sa.Column("added_by",             sa.String(), nullable=False),
        sa.Column("add_reason",           sa.Text(), nullable=True),
        sa.Column("is_substitution",      sa.Boolean(), server_default="false", nullable=False),
        sa.Column("original_item_name",   sa.String(), nullable=True),
        sa.Column("substitution_reason",  sa.Text(), nullable=True),
        sa.Column("user_action",          sa.String(), nullable=True),
        sa.Column("final_quantity",       sa.Numeric(8, 3), nullable=True),
        sa.Column("was_in_stock",         sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at",           sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── loop_run_edits ────────────────────────────────────────────────────────
    op.create_table(
        "loop_run_edits",
        sa.Column("id",           postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("loop_run_id",  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("loop_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("edit_type",    sa.String(), nullable=False),
        sa.Column("item_name",    sa.String(), nullable=False),
        sa.Column("original_qty", sa.Numeric(8, 3), nullable=True),
        sa.Column("new_qty",      sa.Numeric(8, 3), nullable=True),
        sa.Column("edit_reason",  sa.Text(), nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── order_items ───────────────────────────────────────────────────────────
    op.create_table(
        "order_items",
        sa.Column("id",             postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("order_id",       postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id",   postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("households.id"), nullable=False),
        sa.Column("swiggy_sku_id",  sa.String(), nullable=False),
        sa.Column("product_name",   sa.String(), nullable=False),
        sa.Column("brand",          sa.String(), nullable=True),
        sa.Column("category",       sa.String(), nullable=True),
        sa.Column("quantity",       sa.Numeric(8, 3), nullable=False),
        sa.Column("unit",           sa.String(), nullable=False),
        sa.Column("unit_price",     sa.Numeric(10, 2), nullable=False),
        sa.Column("total_price",    sa.Numeric(10, 2), nullable=False),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("order_items")
    op.drop_table("loop_run_edits")
    op.drop_table("loop_run_items")

    op.drop_constraint("fk_orders_loop_run", "orders", type_="foreignkey")
    op.drop_index("idx_loop_runs_state",     "loop_runs")
    op.drop_index("idx_loop_runs_household", "loop_runs")
    op.drop_table("loop_runs")

    op.drop_index("idx_orders_pantry_update", "orders")
    op.drop_table("orders")

    op.drop_index("idx_pantry_category", "pantry_items")
    op.drop_index("idx_pantry_reorder",  "pantry_items")
    op.drop_table("pantry_items")
    op.drop_table("brand_preferences")

    op.drop_constraint("fk_prefs_preferred_address", "household_preferences", type_="foreignkey")
    op.drop_table("addresses")

    op.drop_index("idx_tokens_expiry", "swiggy_tokens")
    op.drop_table("swiggy_tokens")
    op.drop_table("household_members")

    op.drop_index("idx_preferences_next_run", "household_preferences")
    op.drop_table("household_preferences")
    op.drop_table("households")
