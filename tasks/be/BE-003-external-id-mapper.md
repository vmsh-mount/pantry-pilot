# BE-003 — Centralise External ID Mapping

**Status:** ✅ Done  
**Area:** Backend  
**Created:** 2026-07-04

---

## Problem

The codebase has a dual-ID pattern for addresses: an internal UUID (FK in `household_preferences.preferred_address_id`) and a Swiggy string ID (passed to all MCP API calls). Several places in `planning_graph.py` get these mixed up, causing real order-placement failures and FK constraint violations.

The correct lookup pattern already exists in two places (`sense()` and `run_place()` in `planning_service.py`) but is done ad-hoc. There is no single authoritative place that owns the translation, so bugs keep creeping in.

---

## Bugs Identified

| Severity | File | Line(s) | Bug |
|----------|------|---------|-----|
| Critical | `agent/planning_graph.py` | 845 | `place()` reads `preferred_address_id` (UUID) and passes it directly to `client.update_cart(address_id=...)` — Swiggy MCP expects the string ID |
| High | `agent/planning_graph.py` | 870–874 | `place()` fallback stores Swiggy string ID directly into `prefs.preferred_address_id` — FK violation in PostgreSQL |
| Medium | `agent/planning_graph.py` | 539 | `optimize()` falls back to `preferred_address_id` (UUID) when `swiggy_address_id` is None, then passes UUID to `client.search_products()` |
| Low | `services/onboarding_service.py` | 283 | Unreachable code assigns Swiggy string ID into UUID column — latent bug if ever called from a new path |
| Test quality | `tests/integration/test_lifecycle_edge_cases.py` | 309, 358, 477 | Test state puts Swiggy string IDs in UUID slots — SQLite doesn't enforce FK so bugs are masked |
| Test quality | `tests/integration/test_planning_graph.py` | 63 | Same as above |

---

## Correct Pattern (Already Working — Reference)

**`services/planning_service.py` → `run_place()` (lines 204–213):**
1. Read `prefs.preferred_address_id` (UUID)
2. `SELECT * FROM addresses WHERE id = <uuid>`
3. Extract `addr.swiggy_address_id`
4. Store as `state["swiggy_address_id"]` for MCP calls

**`agent/planning_graph.py` → `sense()` (lines 95–104):**
Same 4-step pattern as above.

This is the invariant: **UUIDs live in the DB; Swiggy string IDs go to MCP calls.**

---

## Design

### New file: `app/pilot/app/services/id_mapper.py`

A single `ExternalIdMapper` class that owns all UUID ↔ external-ID translations.

**Key interface:**
- `get_external_id(session, entity_type, system, internal_uuid) → str` — given our UUID, return the Swiggy (or other system) string ID
- `get_or_create_internal_id(session, entity_type, system, external_id, **kwargs) → UUID` — given a Swiggy string ID, return our UUID, creating a DB record if it doesn't exist yet

**Keyed by `(entity_type, system)`:**
- `("address", "swiggy")` — the current use case
- Adding `("store", "swiggy")` or `("address", "blinkit")` later requires no structural change to the mapper

### Extensibility

Only addresses currently need dual mapping. Other entities (products, orders, users) store Swiggy IDs directly — that stays as-is. If a future entity needs the same pattern, it registers a new `(entity_type, system)` pair.

---

## Files to Touch

| File | Change |
|------|--------|
| `app/services/id_mapper.py` | **Create** — new `ExternalIdMapper` class |
| `app/agent/planning_graph.py` | Fix lines 845, 870–874, 539 to use mapper |
| `app/services/onboarding_service.py` | Remove/fix dead code at line 283 |
| `tests/integration/test_lifecycle_edge_cases.py` | Fix test state at lines 309, 358, 477 |
| `tests/integration/test_planning_graph.py` | Fix test state at line 63 |

No schema changes needed — the DB is correctly designed.

---

## Acceptance Criteria

- [ ] `ExternalIdMapper` is the single place that translates between UUIDs and Swiggy address IDs
- [ ] `place()` passes `swiggy_address_id` (string) to all MCP calls — never the UUID
- [ ] `place()` fallback uses mapper's upsert path to get UUID before writing to `prefs.preferred_address_id`
- [ ] `optimize()` does not fall back to UUID; raises a clear error if no Swiggy address ID is resolvable
- [ ] `onboarding_service.py:283` dead code is removed or corrected
- [ ] Test fixtures use proper UUIDs in UUID slots and Swiggy strings in Swiggy slots
- [ ] `make test` passes

---

## Out of Scope

- Migrating other entity types (products, orders, users) to the mapper — they use direct string mapping intentionally
- Schema changes
- Adding any new mapping systems (Blinkit etc.) — just build the extensible foundation
