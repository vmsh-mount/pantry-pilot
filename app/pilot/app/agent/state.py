"""
LangGraph state definitions for the PantryPilot planning loop.

The PlanningState TypedDict is the single shared object that flows through
all five stages: SENSE → PLAN → OPTIMIZE → CONFIRM → PLACE.

Every field is Optional so nodes can be added incrementally and the graph
can be checkpointed and resumed at any stage.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class BasketItem(TypedDict, total=False):
    """A single item in the candidate or resolved basket."""
    item_name:          str
    sku_id:             Optional[str]    # None until OPTIMIZE resolves it
    product_name:       Optional[str]    # Swiggy's display name after resolution
    brand:              Optional[str]
    category:           str
    quantity:           float
    unit:               str
    unit_price:         Optional[float]
    total_price:        Optional[float]
    in_stock:           bool
    added_by:           str             # rules_engine | llm | user_added
    add_reason:         Optional[str]
    is_substitution:    bool
    original_item_name: Optional[str]
    substitution_reason: Optional[str]


class PlanningState(TypedDict, total=False):
    """
    Shared state that flows through the LangGraph planning graph.

    ── Input (set by PlanningService before graph runs) ──────────────────────
    household_id:       str
    loop_run_id:        str
    access_token:       str         # decrypted Swiggy token, lives only in memory
    trigger_type:       str         # scheduled | user_initiated | user_confirmed_whatsapp

    ── SENSE outputs ──────────────────────────────────────────────────────────
    household_profile:  dict        # {member_count, diet_type, allergies, budget_min, budget_max}
    pantry_items:       list[dict]  # serialised PantryItem rows after decay applied
    brand_preferences:  dict        # {item_name: preferred_brand}
    preferred_address_id: str    # our internal UUID FK
    swiggy_address_id:    str    # Swiggy's address ID — used for all MCP calls
    preferred_delivery_slot: str
    whatsapp_number:    str
    week_label:         str         # e.g. "Week of 30 Jun 2026"

    ── PLAN outputs ──────────────────────────────────────────────────────────
    candidate_basket:   list[BasketItem]   # after rules engine
    llm_additions:      list[BasketItem]   # items LLM suggested
    llm_flags:          list[str]          # human-readable notes from LLM
    llm_tokens_used:    int
    llm_latency_ms:     int

    ── OPTIMIZE outputs ──────────────────────────────────────────────────────
    resolved_basket:    list[BasketItem]   # fully resolved with SKU + price
    substitutions_made: int
    items_unavailable:  list[str]          # item names that couldn't be resolved
    estimated_total:    float

    ── CONFIRM outputs ────────────────────────────────────────────────────────
    confirmation_sent_at: str    # ISO timestamp
    user_action:        str      # confirmed | skipped | edited | timeout
    user_edited_basket: Optional[list[BasketItem]]   # if user edited

    ── PLACE outputs ──────────────────────────────────────────────────────────
    swiggy_order_id:    str
    final_total:        float
    estimated_delivery: Optional[str]

    ── Error / flow control ──────────────────────────────────────────────────
    error:              Optional[str]       # set if any stage fails
    error_stage:        Optional[str]       # which stage the error occurred in
    should_abort:       bool                # True = stop graph, mark run failed
    skip_reason:        Optional[str]       # set when flow resolves to skip
    """

    # Input
    household_id:           str
    loop_run_id:            str
    access_token:           str
    trigger_type:           str

    # SENSE
    household_profile:      dict
    pantry_items:           list
    recent_orders:          list   # order history items used when pantry is empty
    brand_preferences:      dict
    preferred_address_id:   str
    swiggy_address_id:      str
    preferred_delivery_slot: str
    whatsapp_number:        str
    week_label:             str

    # PLAN
    candidate_basket:       list
    llm_additions:          list
    llm_flags:              list
    llm_tokens_used:        int
    llm_latency_ms:         int

    # OPTIMIZE
    resolved_basket:        list
    substitutions_made:     int
    items_unavailable:      list
    estimated_total:        float

    # CONFIRM
    confirmation_sent_at:   str
    user_action:            str
    user_edited_basket:     Optional[list]

    # PLACE
    swiggy_order_id:        str
    final_total:            float
    estimated_delivery:     Optional[str]

    # Control
    error:                  Optional[str]
    error_stage:            Optional[str]
    should_abort:           bool
    skip_reason:            Optional[str]
