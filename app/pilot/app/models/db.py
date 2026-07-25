import uuid
from datetime import datetime, time
from sqlalchemy import (
    String, Integer, Float, Boolean, Text, ARRAY,
    Numeric, DateTime, Time, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


# ─────────────────────────────────────────────
# Household
# ─────────────────────────────────────────────
class Household(Base):
    __tablename__ = "households"

    id:                  Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    swiggy_user_id:      Mapped[str] = mapped_column(String, unique=True, nullable=False)
    whatsapp_number:     Mapped[str | None] = mapped_column(String)
    whatsapp_verified:   Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_opted_out:  Mapped[bool] = mapped_column(Boolean, default=False)

    household_type:      Mapped[str | None] = mapped_column(String, nullable=True)  # solo|couple|family|joint_family
    member_count:        Mapped[int] = mapped_column(Integer, default=1)
    diet_type:           Mapped[str | None] = mapped_column(String, nullable=True)  # vegetarian|vegan|jain
    allergies:           Mapped[list] = mapped_column(ARRAY(String), default=list)
    weekly_budget_min:   Mapped[int | None] = mapped_column(Integer)
    weekly_budget_max:   Mapped[int | None] = mapped_column(Integer)

    # Gap-to-Cart dark-launch flag (Phase 0). NULL/false = off.
    nutrition_gaps_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active:           Mapped[bool] = mapped_column(Boolean, default=True)
    is_paused:           Mapped[bool] = mapped_column(Boolean, default=False)
    paused_at:           Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_reason:       Mapped[str | None] = mapped_column(String)

    city:                Mapped[str] = mapped_column(String, default="Bengaluru")
    created_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    preferences:    Mapped["HouseholdPreferences"] = relationship(back_populates="household", uselist=False)
    members:        Mapped[list["HouseholdMember"]] = relationship(back_populates="household")
    token:          Mapped["SwiggyToken"] = relationship(back_populates="household", uselist=False)
    addresses:      Mapped[list["Address"]] = relationship(back_populates="household")
    brand_prefs:    Mapped[list["BrandPreference"]] = relationship(back_populates="household")
    pantry_items:   Mapped[list["PantryItem"]] = relationship(back_populates="household")
    loop_runs:      Mapped[list["LoopRun"]] = relationship(back_populates="household")
    orders:         Mapped[list["Order"]] = relationship(back_populates="household")
    routines:       Mapped[list["Routine"]] = relationship(back_populates="household")


# ─────────────────────────────────────────────
# Household Preferences
# ─────────────────────────────────────────────
class HouseholdPreferences(Base):
    __tablename__ = "household_preferences"

    id:                      Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:            Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), unique=True)

    preferred_order_day:     Mapped[str] = mapped_column(String, default="sunday")
    preferred_order_time:    Mapped[time] = mapped_column(Time, default=time(10, 0))

    freq_staples:            Mapped[str] = mapped_column(String, default="weekly")
    freq_fresh_produce:      Mapped[str] = mapped_column(String, default="weekly")
    freq_dairy_eggs:         Mapped[str] = mapped_column(String, default="weekly")
    freq_packaged:           Mapped[str] = mapped_column(String, default="weekly")

    preferred_address_id:    Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("addresses.id"))
    preferred_delivery_slot: Mapped[str] = mapped_column(String, default="evening")
    confirmation_window_hrs: Mapped[int] = mapped_column(Integer, default=4)

    next_run_at:             Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at:             Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_external_sync_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at:              Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:              Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:               Mapped["Household"] = relationship(back_populates="preferences")

    __table_args__ = (
        Index("idx_preferences_next_run", "next_run_at"),
    )


# ─────────────────────────────────────────────
# Household Members
# ─────────────────────────────────────────────
class HouseholdMember(Base):
    __tablename__ = "household_members"

    id:             Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:   Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))
    role:           Mapped[str | None] = mapped_column(String)  # adult|child|elderly|infant
    diet_override:  Mapped[str | None] = mapped_column(String)

    # V2 nutrition fields
    age_years:      Mapped[int | None] = mapped_column(Integer)
    sex:            Mapped[str | None] = mapped_column(String)
    weight_kg:      Mapped[float | None] = mapped_column(Numeric(5, 1))
    height_cm:      Mapped[float | None] = mapped_column(Numeric(5, 1))
    activity_level: Mapped[str | None] = mapped_column(String)
    health_flags:   Mapped[list] = mapped_column(ARRAY(String), default=list)

    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:      Mapped["Household"] = relationship(back_populates="members")


# ─────────────────────────────────────────────
# Swiggy Tokens
# ─────────────────────────────────────────────
class SwiggyToken(Base):
    __tablename__ = "swiggy_tokens"

    id:                  Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:        Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), unique=True)

    access_token_enc:    Mapped[str] = mapped_column(Text, nullable=False)  # AES-256 encrypted
    token_expiry:        Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    nudge_48hr_sent:     Mapped[bool] = mapped_column(Boolean, default=False)
    nudge_24hr_sent:     Mapped[bool] = mapped_column(Boolean, default=False)
    nudge_expired_sent:  Mapped[bool] = mapped_column(Boolean, default=False)

    created_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    household:           Mapped["Household"] = relationship(back_populates="token")

    __table_args__ = (
        Index("idx_tokens_expiry", "token_expiry"),
    )


# ─────────────────────────────────────────────
# Addresses
# ─────────────────────────────────────────────
class Address(Base):
    __tablename__ = "addresses"

    id:                 Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:       Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))
    swiggy_address_id:  Mapped[str] = mapped_column(String, nullable=False)
    label:              Mapped[str | None] = mapped_column(String)
    area:               Mapped[str | None] = mapped_column(String)
    city:               Mapped[str] = mapped_column(String, default="Bengaluru")
    is_default:         Mapped[bool] = mapped_column(Boolean, default=False)
    created_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    household:          Mapped["Household"] = relationship(back_populates="addresses")

    __table_args__ = (
        UniqueConstraint("household_id", "swiggy_address_id"),
    )


# ─────────────────────────────────────────────
# Brand Preferences
# ─────────────────────────────────────────────
class BrandPreference(Base):
    __tablename__ = "brand_preferences"

    id:              Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:    Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))
    category:        Mapped[str] = mapped_column(String, nullable=False)
    item_name:       Mapped[str] = mapped_column(String, nullable=False)
    preferred_brand: Mapped[str] = mapped_column(String, nullable=False)
    confidence:      Mapped[float | None] = mapped_column(Numeric(3, 2))

    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:       Mapped["Household"] = relationship(back_populates="brand_prefs")

    __table_args__ = (
        UniqueConstraint("household_id", "item_name"),
    )


# ─────────────────────────────────────────────
# Pantry Items
# ─────────────────────────────────────────────
class PantryItem(Base):
    __tablename__ = "pantry_items"

    id:                      Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:            Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))

    item_name:               Mapped[str] = mapped_column(String, nullable=False)
    category:                Mapped[str] = mapped_column(String, nullable=False)
    standard_unit:           Mapped[str] = mapped_column(String, nullable=False)

    last_ordered_qty:        Mapped[float | None] = mapped_column(Numeric(8, 3))
    last_ordered_at:         Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_qty_remaining: Mapped[float] = mapped_column(Numeric(8, 3), default=0)
    reorder_threshold:       Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)

    avg_weekly_consumption:  Mapped[float | None] = mapped_column(Numeric(8, 3))
    consumption_confidence:  Mapped[float] = mapped_column(Numeric(3, 2), default=0.0)

    times_ordered:           Mapped[int] = mapped_column(Integer, default=0)
    times_removed_by_user:   Mapped[int] = mapped_column(Integer, default=0)
    times_kept_by_user:      Mapped[int] = mapped_column(Integer, default=0)
    last_user_action:        Mapped[str | None] = mapped_column(String)
    last_user_action_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active:               Mapped[bool] = mapped_column(Boolean, default=True)

    created_at:              Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:              Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:               Mapped["Household"] = relationship(back_populates="pantry_items")

    __table_args__ = (
        UniqueConstraint("household_id", "item_name"),
        Index("idx_pantry_reorder", "household_id", "estimated_qty_remaining", "reorder_threshold"),
        Index("idx_pantry_category", "household_id", "category"),
    )


# ─────────────────────────────────────────────
# Loop Runs
# ─────────────────────────────────────────────
class LoopRun(Base):
    __tablename__ = "loop_runs"

    id:                     Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:           Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"))

    triggered_at:           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    trigger_type:           Mapped[str] = mapped_column(String, default="scheduled")

    state:                  Mapped[str] = mapped_column(String, default="pending")

    sense_started_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sense_completed_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_started_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_completed_at:      Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    optimize_started_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    optimize_completed_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirm_sent_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirm_responded_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    place_started_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    place_completed_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user_action:            Mapped[str | None] = mapped_column(String)
    time_to_respond_sec:    Mapped[int | None] = mapped_column(Integer)

    order_id:               Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("orders.id"))
    skip_reason:            Mapped[str | None] = mapped_column(String)
    failure_reason:         Mapped[str | None] = mapped_column(String)
    failure_stage:          Mapped[str | None] = mapped_column(String)

    llm_model:              Mapped[str | None] = mapped_column(String)
    llm_tokens_used:        Mapped[int | None] = mapped_column(Integer)
    llm_latency_ms:         Mapped[int | None] = mapped_column(Integer)

    substitutions_count:    Mapped[int] = mapped_column(Integer, default=0)
    items_unavailable:      Mapped[int] = mapped_column(Integer, default=0)

    created_at:             Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:             Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:              Mapped["Household"] = relationship(back_populates="loop_runs")
    items:                  Mapped[list["LoopRunItem"]] = relationship(back_populates="loop_run")
    edits:                  Mapped[list["LoopRunEdit"]] = relationship(back_populates="loop_run")

    __table_args__ = (
        Index("idx_loop_runs_household", "household_id"),
        Index("idx_loop_runs_state", "state"),
    )


# ─────────────────────────────────────────────
# Loop Run Items
# ─────────────────────────────────────────────
class LoopRunItem(Base):
    __tablename__ = "loop_run_items"

    id:                   Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    loop_run_id:          Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("loop_runs.id", ondelete="CASCADE"))
    household_id:         Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"))

    item_name:            Mapped[str] = mapped_column(String, nullable=False)
    swiggy_sku_id:        Mapped[str | None] = mapped_column(String)
    swiggy_product_name:  Mapped[str | None] = mapped_column(String)
    brand:                Mapped[str | None] = mapped_column(String)

    quantity:             Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    unit:                 Mapped[str] = mapped_column(String, nullable=False)
    unit_price:           Mapped[float | None] = mapped_column(Numeric(10, 2))
    total_price:          Mapped[float | None] = mapped_column(Numeric(10, 2))

    added_by:             Mapped[str] = mapped_column(String, nullable=False)  # rules_engine|llm|user_added
    add_reason:           Mapped[str | None] = mapped_column(Text)

    is_substitution:      Mapped[bool] = mapped_column(Boolean, default=False)
    original_item_name:   Mapped[str | None] = mapped_column(String)
    substitution_reason:  Mapped[str | None] = mapped_column(Text)

    user_action:          Mapped[str | None] = mapped_column(String)  # kept|removed|qty_changed
    final_quantity:       Mapped[float | None] = mapped_column(Numeric(8, 3))
    was_in_stock:         Mapped[bool] = mapped_column(Boolean, default=True)

    created_at:           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    loop_run:             Mapped["LoopRun"] = relationship(back_populates="items")


# ─────────────────────────────────────────────
# Loop Run Edits
# ─────────────────────────────────────────────
class LoopRunEdit(Base):
    __tablename__ = "loop_run_edits"

    id:              Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    loop_run_id:     Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("loop_runs.id", ondelete="CASCADE"))
    household_id:    Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"))

    edit_type:       Mapped[str] = mapped_column(String, nullable=False)  # remove_item|add_item|change_qty
    item_name:       Mapped[str] = mapped_column(String, nullable=False)
    original_qty:    Mapped[float | None] = mapped_column(Numeric(8, 3))
    new_qty:         Mapped[float | None] = mapped_column(Numeric(8, 3))
    edit_reason:     Mapped[str | None] = mapped_column(Text)

    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    loop_run:        Mapped["LoopRun"] = relationship(back_populates="edits")


# ─────────────────────────────────────────────
# Orders
# ─────────────────────────────────────────────
class Order(Base):
    __tablename__ = "orders"

    id:                 Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:       Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"))
    loop_run_id:        Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("loop_runs.id"))

    swiggy_order_id:    Mapped[str] = mapped_column(String, unique=True, nullable=False)
    swiggy_address_id:  Mapped[str] = mapped_column(String, nullable=False)
    delivery_slot:      Mapped[str | None] = mapped_column(String)

    item_total:         Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    delivery_fee:       Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    taxes:              Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    grand_total:        Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    status:             Mapped[str] = mapped_column(String, default="placed")
    source:             Mapped[str] = mapped_column(String, nullable=False, server_default="flow")  # "flow"|"quick_order"

    placed_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pantry_updated:     Mapped[bool] = mapped_column(Boolean, default=False)
    pantry_updated_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:          Mapped["Household"] = relationship(back_populates="orders")
    items:              Mapped[list["OrderItem"]] = relationship(back_populates="order")

    __table_args__ = (
        Index("idx_orders_pantry_update", "pantry_updated", "placed_at"),
    )


# ─────────────────────────────────────────────
# Order Items
# ─────────────────────────────────────────────
class OrderItem(Base):
    __tablename__ = "order_items"

    id:               Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    order_id:         Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("orders.id", ondelete="CASCADE"))
    household_id:     Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"))

    swiggy_sku_id:    Mapped[str] = mapped_column(String, nullable=False)
    product_name:     Mapped[str] = mapped_column(String, nullable=False)
    brand:            Mapped[str | None] = mapped_column(String)
    category:         Mapped[str | None] = mapped_column(String)

    quantity:         Mapped[float] = mapped_column(Numeric(8, 3), nullable=False)
    unit:             Mapped[str] = mapped_column(String, nullable=False)
    unit_price:       Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    total_price:      Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    created_at:       Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order:            Mapped["Order"] = relationship(back_populates="items")


# ─────────────────────────────────────────────
# Routines
# ─────────────────────────────────────────────
class Routine(Base):
    __tablename__ = "routines"

    id:                 Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:       Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"))
    name:               Mapped[str] = mapped_column(String, nullable=False)
    status:             Mapped[str] = mapped_column(String, default="active")  # active|paused|ended|deleted
    frequency_type:     Mapped[str] = mapped_column(String, nullable=False)    # every_n_days|weekly|monthly
    frequency_value:    Mapped[int] = mapped_column(Integer, nullable=False)   # N days | 0-6 weekday | 1-28 day-of-month
    schedule_time:      Mapped[time] = mapped_column(Time, nullable=False)     # stored as UTC
    start_date:         Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date:           Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # null = ongoing
    next_run_at:        Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at:          Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_days_paused:  Mapped[int] = mapped_column(Integer, default=0)
    created_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at:         Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    household:          Mapped["Household"] = relationship(back_populates="routines")
    items:              Mapped[list["RoutineItem"]] = relationship(back_populates="routine", cascade="all, delete-orphan")
    runs:               Mapped[list["RoutineRun"]] = relationship(back_populates="routine")

    __table_args__ = (
        Index("idx_routines_household_status", "household_id", "status"),
        Index("idx_routines_next_run_at", "next_run_at"),
    )


class RoutineItem(Base):
    __tablename__ = "routine_items"

    id:                   Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    routine_id:           Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("routines.id", ondelete="RESTRICT"))
    item_name:            Mapped[str] = mapped_column(String, nullable=False)
    quantity:             Mapped[float] = mapped_column(Numeric(8, 3), nullable=False, default=1)
    unit:                 Mapped[str] = mapped_column(String, default="unit")
    swiggy_product_id:    Mapped[str | None] = mapped_column(String)
    swiggy_product_name:  Mapped[str | None] = mapped_column(String)
    created_at:           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    routine:              Mapped["Routine"] = relationship(back_populates="items")


class RoutineRun(Base):
    __tablename__ = "routine_runs"

    id:            Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    routine_id:    Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("routines.id", ondelete="RESTRICT"))
    scheduled_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status:        Mapped[str] = mapped_column(String, nullable=False)  # placed|partial|failed|skipped
    order_id:      Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("orders.id"))
    skipped_items: Mapped[str | None] = mapped_column(Text)  # JSON string: [{item_name, reason}]
    skip_reason:   Mapped[str | None] = mapped_column(String)  # user_skip|all_items_unavailable|token_expired|lock_timeout|missed|household_paused
    placed_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_amount:  Mapped[float | None] = mapped_column(Numeric(10, 2))
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    routine:       Mapped["Routine"] = relationship(back_populates="runs")

    __table_args__ = (
        Index("idx_routine_runs_routine_id", "routine_id"),
    )


# ─────────────────────────────────────────────
# Flow: Household Model
# ─────────────────────────────────────────────
class HouseholdModel(Base):
    __tablename__ = "household_models"

    id:                      Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:            Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), unique=True, nullable=False)

    anchors:                 Mapped[dict] = mapped_column(JSONB, default=list)   # [{item_name, status, promoted_by, promoted_at}]
    preferences:             Mapped[dict] = mapped_column(JSONB, default=dict)   # per-category: {loyalty_score, preferred_brand, known_brands, rejected_brands, oos_fallbacks}
    confirmation_behaviour:  Mapped[dict] = mapped_column(JSONB, default=dict)   # {typical_confirm_hour_start, typical_confirm_hour_end, typical_confirm_days, avg_response_lag_minutes, preferred_delivery_lead_hours}

    avg_edit_count:          Mapped[float | None] = mapped_column(Float, nullable=True)
    reorder_horizon_days:    Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_evaluated_at:       Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_updated:            Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ─────────────────────────────────────────────
# Flow: Flow Basket
# ─────────────────────────────────────────────
class FlowBasket(Base):
    __tablename__ = "flow_baskets"

    id:               Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:     Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), nullable=False)
    loop_run_id:      Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("loop_runs.id"), nullable=True)

    generated_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validated_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generated_items:  Mapped[list] = mapped_column(JSONB, default=list)   # [{item_name, sku_id, brand, quantity, unit, price_at_generation}]
    validated_items:  Mapped[list] = mapped_column(JSONB, default=list)
    dropped_items:    Mapped[list] = mapped_column(JSONB, default=list)   # [{item_name, reason: "oos"|"already_ordered"}]

    status:           Mapped[str] = mapped_column(String, default="held")  # "held"|"delivered"|"confirmed"|"expired"|"replaced"


# ─────────────────────────────────────────────
# Flow: Item Signal
# ─────────────────────────────────────────────
class ItemSignal(Base):
    __tablename__ = "item_signals"

    id:              Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:    Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), nullable=False)
    loop_run_id:     Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("loop_runs.id"), nullable=True)

    item_name:       Mapped[str] = mapped_column(String, nullable=False)
    signal_type:     Mapped[str] = mapped_column(String, nullable=False)  # "added"|"removed"|"qty_increased"|"qty_decreased"|"brand_changed"|"accepted"
    source:          Mapped[str] = mapped_column(String, nullable=False, server_default="flow")  # "flow"|"quick_order"

    previous_value:  Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value:       Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    recorded_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────
# Nutrition: Cache (per-SKU, global)
# ─────────────────────────────────────────────
class NutritionCache(Base):
    __tablename__ = "nutrition_cache"

    id:                    Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    sku_id:                Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    resolved_at:           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Source + confidence
    # source:     'off' | 'usda' | 'llm' | 'user_photo' | 'manual'
    # confidence: 'high' | 'medium' | 'estimate' | 'verified'
    source:                Mapped[str] = mapped_column(String(20), nullable=False)
    confidence:            Mapped[str] = mapped_column(String(10), nullable=False)

    # Quantity normalization
    quantity_g:            Mapped[float | None] = mapped_column(Float)
    quantity_unresolvable: Mapped[bool] = mapped_column(Boolean, default=False)
    serving_size_g:        Mapped[float | None] = mapped_column(Float)  # declared serving size; needed for diabetic carbs-per-serving check

    # Fixed SQL columns — only nutrients aggregated in queries
    calories_per_100g:     Mapped[float | None] = mapped_column(Float)
    protein_per_100g:      Mapped[float | None] = mapped_column(Float)
    total_carbs_per_100g:  Mapped[float | None] = mapped_column(Float)
    fat_per_100g:          Mapped[float | None] = mapped_column(Float)
    fiber_per_100g:        Mapped[float | None] = mapped_column(Float)
    sodium_mg_per_100g:    Mapped[float | None] = mapped_column(Float)  # mg per 100g

    # All other nutrients: sugar, saturated_fat, trans_fat, cholesterol, calcium,
    # iron, potassium, vitamin_c, vitamin_d, vitamin_b12, folate, zinc, omega3, ...
    # All values per 100g, numeric, null if unknown. Extend freely — no migration needed.
    nutrients:             Mapped[dict] = mapped_column(JSONB, default=dict)

    # Source metadata
    nutriscore_grade:      Mapped[str | None] = mapped_column(String(1))
    matched_name:          Mapped[str | None] = mapped_column(Text)
    off_product_id:        Mapped[str | None] = mapped_column(Text)
    usda_fdc_id:           Mapped[int | None] = mapped_column(Integer)
    raw_data:              Mapped[dict | None] = mapped_column(JSONB)

    # Gap-to-Cart (Phase B1): canonical food + what it's a meaningful source of.
    # food_concept: brand-stripped food, e.g. "Nandini Toned Milk" -> "milk"
    # notable_nutrients: e.g. ["protein", "calcium"] — grouping keys from NUTRIENT_KEYS
    food_concept:          Mapped[str | None] = mapped_column(String(60))
    notable_nutrients:     Mapped[list] = mapped_column(JSONB, default=list)

    __table_args__ = (
        Index("idx_nutrition_cache_sku_id", "sku_id"),
    )


# ─────────────────────────────────────────────
# Nutrition: Per-order totals
# ─────────────────────────────────────────────
class OrderNutrition(Base):
    __tablename__ = "order_nutrition"

    id:                    Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    order_id:              Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False)
    household_id:          Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id"), nullable=False)
    computed_at:           Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Fixed aggregates used in weekly trend queries and compliance checks
    total_calories:        Mapped[float | None] = mapped_column(Float)
    total_protein_g:       Mapped[float | None] = mapped_column(Float)
    total_carbs_g:         Mapped[float | None] = mapped_column(Float)
    total_fat_g:           Mapped[float | None] = mapped_column(Float)
    total_fiber_g:         Mapped[float | None] = mapped_column(Float)
    total_sodium_mg:       Mapped[float | None] = mapped_column(Float)

    # All other nutrient totals mirroring nutrition_cache.nutrients keys, summed across items
    nutrient_totals:       Mapped[dict] = mapped_column(JSONB, default=dict)

    # Resolution coverage
    total_items:           Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_items:        Mapped[int] = mapped_column(Integer, nullable=False)
    high_confidence_items: Mapped[int] = mapped_column(Integer, default=0)
    llm_estimated_items:   Mapped[int] = mapped_column(Integer, default=0)
    unresolved_items:      Mapped[int] = mapped_column(Integer, default=0)

    # Per-item breakdown: [{item_name, sku_id, source, confidence, quantity_g,
    #   calories, protein_g, carbs_g, fat_g, fiber_g, sodium_mg, nutrients: {...}}]
    item_breakdown:        Mapped[list] = mapped_column(JSONB, default=list)

    __table_args__ = (
        Index("idx_order_nutrition_household_id", "household_id"),
        Index("idx_order_nutrition_order_id", "order_id"),
    )


# ─────────────────────────────────────────────
# Nutrition: Household goal overrides
# ─────────────────────────────────────────────
class HouseholdNutritionGoals(Base):
    __tablename__ = "household_nutrition_goals"

    id:               Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    household_id:     Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("households.id", ondelete="CASCADE"), unique=True, nullable=False)

    daily_calories:   Mapped[int | None] = mapped_column(Integer)
    daily_protein_g:  Mapped[int | None] = mapped_column(Integer)
    daily_fiber_g:    Mapped[int | None] = mapped_column(Integer)
    daily_sodium_mg:  Mapped[int | None] = mapped_column(Integer)

    updated_at:       Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ─────────────────────────────────────────────
# Nutrition: Gap-to-Cart — learned nutrient -> food map
# ─────────────────────────────────────────────
class NutrientFoodCandidate(Base):
    """
    Materialized output of Phase B2's nightly aggregation job.
    `nutrient` must be one of the canonical values in NUTRIENT_KEYS
    (app/services/nutrition_resolution.py) — not DB-enforced, guarded by a test.
    No price column: nutrient_per_rupee is computed at request time (Phase B3)
    from a live Swiggy search price, never stored here.
    """
    __tablename__ = "nutrient_food_candidate"

    id:                    Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    nutrient:              Mapped[str] = mapped_column(String(20), nullable=False)
    food_concept:          Mapped[str] = mapped_column(String(60), nullable=False)
    diet_tags:             Mapped[list] = mapped_column(ARRAY(String), default=list)

    nutrient_per_100g:     Mapped[float | None] = mapped_column(Float)
    representative_sku_id: Mapped[str | None] = mapped_column(String(50))

    order_frequency:       Mapped[int] = mapped_column(Integer, default=0)
    repurchase_rate:       Mapped[float | None] = mapped_column(Float)

    confidence:            Mapped[str | None] = mapped_column(String(10))
    sample_size:           Mapped[int] = mapped_column(Integer, default=0)
    last_refreshed:        Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_nfc_nutrient", "nutrient"),
        Index("idx_nfc_diet_tags", "diet_tags", postgresql_using="gin"),
    )
