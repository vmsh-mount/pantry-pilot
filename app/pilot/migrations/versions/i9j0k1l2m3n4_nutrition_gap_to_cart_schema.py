"""nutrition_gap_to_cart_schema

Phase 0 of the nutrition Gap-to-Cart series. Pure schema — no behavior change.
See docs/nutrition-gap-to-cart/implementation-plan.md and
tasks/features/nutrition-gap-to-cart-phase0-schema.md.

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # nutrition_cache — B1 writes these
    op.add_column("nutrition_cache", sa.Column("food_concept", sa.String(60), nullable=True))
    op.add_column(
        "nutrition_cache",
        sa.Column("notable_nutrients", JSONB, nullable=False, server_default="[]"),
    )

    # nutrient_food_candidate — Phase B2's nightly aggregation output
    op.create_table(
        "nutrient_food_candidate",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("nutrient", sa.String(20), nullable=False),
        sa.Column("food_concept", sa.String(60), nullable=False),
        sa.Column("diet_tags", sa.ARRAY(sa.String), nullable=False, server_default="{}"),

        sa.Column("nutrient_per_100g", sa.Float, nullable=True),
        sa.Column("representative_sku_id", sa.String(50), nullable=True),

        sa.Column("order_frequency", sa.Integer, nullable=False, server_default="0"),
        sa.Column("repurchase_rate", sa.Float, nullable=True),

        sa.Column("confidence", sa.String(10), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_refreshed", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_nfc_nutrient", "nutrient_food_candidate", ["nutrient"])
    op.create_index(
        "idx_nfc_diet_tags", "nutrient_food_candidate", ["diet_tags"],
        postgresql_using="gin",
    )

    # households — dark-launch flag for B3/B4
    op.add_column(
        "households",
        sa.Column("nutrition_gaps_enabled", sa.Boolean, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("households", "nutrition_gaps_enabled")

    op.drop_index("idx_nfc_diet_tags", table_name="nutrient_food_candidate")
    op.drop_index("idx_nfc_nutrient", table_name="nutrient_food_candidate")
    op.drop_table("nutrient_food_candidate")

    op.drop_column("nutrition_cache", "notable_nutrients")
    op.drop_column("nutrition_cache", "food_concept")
