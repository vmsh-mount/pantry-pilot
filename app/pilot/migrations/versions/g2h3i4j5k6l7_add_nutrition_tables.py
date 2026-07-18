"""add_nutrition_tables

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "g2h3i4j5k6l7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nutrition_cache",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("sku_id", sa.String(50), nullable=False, unique=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),

        sa.Column("quantity_g", sa.Float, nullable=True),
        sa.Column("quantity_unresolvable", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("serving_size_g", sa.Float, nullable=True),

        sa.Column("calories_per_100g", sa.Float, nullable=True),
        sa.Column("protein_per_100g", sa.Float, nullable=True),
        sa.Column("total_carbs_per_100g", sa.Float, nullable=True),
        sa.Column("fat_per_100g", sa.Float, nullable=True),
        sa.Column("fiber_per_100g", sa.Float, nullable=True),
        sa.Column("sodium_mg_per_100g", sa.Float, nullable=True),

        sa.Column("nutrients", JSONB, nullable=False, server_default="{}"),

        sa.Column("nutriscore_grade", sa.String(1), nullable=True),
        sa.Column("matched_name", sa.Text, nullable=True),
        sa.Column("off_product_id", sa.Text, nullable=True),
        sa.Column("usda_fdc_id", sa.Integer, nullable=True),
        sa.Column("raw_data", JSONB, nullable=True),
    )
    op.create_index("idx_nutrition_cache_sku_id", "nutrition_cache", ["sku_id"])

    op.create_table(
        "order_nutrition",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("order_id", UUID(as_uuid=False), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("household_id", UUID(as_uuid=False), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        sa.Column("total_calories", sa.Float, nullable=True),
        sa.Column("total_protein_g", sa.Float, nullable=True),
        sa.Column("total_carbs_g", sa.Float, nullable=True),
        sa.Column("total_fat_g", sa.Float, nullable=True),
        sa.Column("total_fiber_g", sa.Float, nullable=True),
        sa.Column("total_sodium_mg", sa.Float, nullable=True),

        sa.Column("nutrient_totals", JSONB, nullable=False, server_default="{}"),

        sa.Column("total_items", sa.Integer, nullable=False),
        sa.Column("resolved_items", sa.Integer, nullable=False),
        sa.Column("high_confidence_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("llm_estimated_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unresolved_items", sa.Integer, nullable=False, server_default="0"),

        sa.Column("item_breakdown", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("idx_order_nutrition_household_id", "order_nutrition", ["household_id"])
    op.create_index("idx_order_nutrition_order_id", "order_nutrition", ["order_id"])

    op.create_table(
        "household_nutrition_goals",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("household_id", UUID(as_uuid=False), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("daily_calories", sa.Integer, nullable=True),
        sa.Column("daily_protein_g", sa.Integer, nullable=True),
        sa.Column("daily_fiber_g", sa.Integer, nullable=True),
        sa.Column("daily_sodium_mg", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("household_nutrition_goals")
    op.drop_index("idx_order_nutrition_order_id", "order_nutrition")
    op.drop_index("idx_order_nutrition_household_id", "order_nutrition")
    op.drop_table("order_nutrition")
    op.drop_index("idx_nutrition_cache_sku_id", "nutrition_cache")
    op.drop_table("nutrition_cache")
