"""
AI ordering assistant — in-app chat API.
tasks/features/ai-ordering-assistant.md, Design §2.

POST /v1/assistant/message — two shapes of call:
  - A fresh user message ({"message": str}) runs the model against recent
    history + the tool set. A read-only tool call executes inline and
    returns the model's natural-language follow-up in the same request. A
    write-tool call is PROPOSED, not executed — the response describes it
    for the frontend to render as a preview card with Confirm/Cancel.
  - A confirmation ({"confirm_tool_call_id": str}) executes that specific,
    previously-proposed tool call now, and returns the model's follow-up.
    Cancel has no backend counterpart — the frontend just drops the
    proposal (Design §4); nothing is "confirmed" unless this is called.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Depends
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.common import APIResponse
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])

_HISTORY_LIMIT = 20  # trailing turns loaded as context — see Design §1's note on bounding this later

_SYSTEM_PROMPT = """\
You are PantryPilot's in-app ordering assistant. You can check nutrition status, \
list and manage Routines (recurring scheduled orders), and search/add/remove items \
in the household's Quick Order basket, including checkout.

Ground rules:
- Never claim an action is done before you've actually called the matching tool. \
If you're about to create, edit, pause, resume, or delete a Routine, add or remove \
a basket item, or check out, call the tool — don't just describe it in text.
- For anything a tool needs that the user hasn't given you (e.g. a routine's \
schedule, or which item to remove when the basket has several similar ones), ask \
a short clarifying question instead of guessing.
- Keep responses brief and conversational — this is a chat, not a report.
"""


class MessageRequest(BaseModel):
    message: str | None = None
    confirm_tool_call_id: str | None = None


def _household_id(request: Request) -> str | None:
    return request.session.get("household_id")


# ── Message <-> Anthropic format translation ─────────────────────────────────

def _row_to_anthropic_message(row) -> dict | None:
    """One AssistantMessage row -> one Anthropic message dict, or None for
    rows that don't map to a turn on their own (there are none currently,
    but keeps this function's contract honest if that changes)."""
    if row.role == "user":
        return {"role": "user", "content": row.content}
    if row.role == "assistant":
        if row.tool_calls:
            call = row.tool_calls
            return {"role": "assistant", "content": [
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]},
            ]}
        return {"role": "assistant", "content": row.content}
    if row.role == "tool_result":
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": row.tool_calls["tool_use_id"], "content": row.content},
        ]}
    return None


async def _load_history(db: AsyncSession, household_id: str) -> list[dict]:
    from app.models.db import AssistantMessage
    result = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.household_id == household_id)
        .order_by(AssistantMessage.created_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    rows = list(reversed(result.scalars().all()))
    messages = [_row_to_anthropic_message(r) for r in rows]
    return [m for m in messages if m is not None]


async def _save(db: AsyncSession, household_id: str, role: str, content: str, tool_calls: dict | None = None):
    from app.models.db import AssistantMessage
    row = AssistantMessage(household_id=household_id, role=role, content=content, tool_calls=tool_calls)
    db.add(row)
    await db.commit()
    return row


# ── Human-readable proposal previews ─────────────────────────────────────────

def _preview_for_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "create_routine":
        items = ", ".join(i.get("item_name", "?") for i in tool_input.get("items", []))
        return f"Create routine \"{tool_input.get('name', '?')}\" — {tool_input.get('frequency_type', '?')} at {tool_input.get('schedule_time', '?')} — {items}"
    if tool_name == "edit_routine":
        changes = ", ".join(f"{k}={v}" for k, v in tool_input.items() if k != "routine_id" and k != "items")
        return f"Update routine — {changes or 'items'}"
    if tool_name == "pause_routine":
        return "Pause this routine"
    if tool_name == "resume_routine":
        return "Resume this routine"
    if tool_name == "delete_routine":
        return "Delete this routine permanently"
    if tool_name == "search_and_add_to_basket":
        qty = tool_input.get("quantity", 1)
        return f"Add {qty} x \"{tool_input.get('query', '?')}\" to your basket"
    if tool_name == "remove_from_basket":
        return f"Remove \"{tool_input.get('item_name', '?')}\" from your basket"
    if tool_name == "checkout_basket":
        return "Place your order now"
    return f"{tool_name}({tool_input})"


# ── Core turn logic ───────────────────────────────────────────────────────────

async def _call_model(db: AsyncSession, household_id: str) -> dict:
    """Run the model against current history, persist what it produced, and
    return the API response shape. Shared by the fresh-message and
    confirm paths — confirm just appends a tool_result row to history
    first, then calls this the same way."""
    from app.providers.llm.anthropic import complete_with_tools
    from app.services import assistant_tools

    history = await _load_history(db, household_id)
    result = await complete_with_tools(
        system=_SYSTEM_PROMPT, messages=history, tools=assistant_tools.TOOLS,
    )

    if not result.is_tool_call:
        await _save(db, household_id, "assistant", result.text or "")
        return {"type": "text", "message": result.text or ""}

    if result.tool_name in assistant_tools.READ_TOOLS:
        # Executes inline — the model asked a read-only question, nothing to confirm.
        await _save(db, household_id, "assistant", "", {
            "id": result.tool_use_id, "name": result.tool_name, "input": result.tool_input,
        })
        tool_result = await assistant_tools.execute_tool(
            result.tool_name, result.tool_input, household_id, db,
        )
        # jsonable_encoder, not raw json.dumps — tool results can contain
        # datetimes (e.g. RoutineOut.model_dump()'s created_at/next_run_at),
        # which json.dumps can't serialize on its own. The REST routes get
        # this for free via FastAPI's response_model pipeline; this is the
        # one place in the app building a JSON string by hand instead of
        # returning a dict through that pipeline, so it needs the same
        # encoder explicitly. Caught by a test with a tool result that
        # actually contains a datetime, not found by inspection.
        await _save(db, household_id, "tool_result", json.dumps(jsonable_encoder(tool_result)), {
            "tool_use_id": result.tool_use_id,
        })
        # Recurse once for the model's natural-language read of the result.
        # Read tools never propose further writes in the same turn in v1's
        # tool set (Design §3) — one extra call is enough, not a loop.
        return await _call_model(db, household_id)

    # Write tool — propose, do not execute. preview is stored alongside
    # id/name/input (not just returned in this response) so a page reload
    # can reconstruct a pending proposal's preview text from GET /history
    # without reimplementing _preview_for_tool's formatting in the frontend.
    preview = _preview_for_tool(result.tool_name, result.tool_input)
    await _save(db, household_id, "assistant", "", {
        "id": result.tool_use_id, "name": result.tool_name, "input": result.tool_input,
        "preview": preview,
    })
    return {
        "type": "proposal",
        "tool_call_id": result.tool_use_id,
        "tool_name": result.tool_name,
        "tool_input": result.tool_input,
        "preview": preview,
    }


@router.post("/message", response_model=APIResponse)
async def send_message(request: Request, body: MessageRequest, db: AsyncSession = Depends(get_db)):
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import AssistantMessage
    from app.services import assistant_tools

    if body.confirm_tool_call_id:
        # Find the proposal this confirms — must be a write-tool call this
        # household actually proposed, and not already executed (guards
        # against a double-tap re-running checkout_basket twice).
        proposal_result = await db.execute(
            select(AssistantMessage)
            .where(
                AssistantMessage.household_id == household_id,
                AssistantMessage.role == "assistant",
            )
            .order_by(AssistantMessage.created_at.desc())
        )
        proposal = next(
            (r for r in proposal_result.scalars().all()
             if r.tool_calls and r.tool_calls.get("id") == body.confirm_tool_call_id),
            None,
        )
        if not proposal:
            return APIResponse.fail("NOT_FOUND", "No matching proposal found.")

        already_executed = (await db.execute(
            select(AssistantMessage).where(
                AssistantMessage.household_id == household_id,
                AssistantMessage.role == "tool_result",
            )
        )).scalars().all()
        if any(
            r.tool_calls and r.tool_calls.get("tool_use_id") == body.confirm_tool_call_id
            for r in already_executed
        ):
            return APIResponse.fail("ALREADY_EXECUTED", "This action was already confirmed.")

        tool_name = proposal.tool_calls["name"]
        tool_input = proposal.tool_calls["input"]
        tool_result = await assistant_tools.execute_tool(tool_name, tool_input, household_id, db)
        # jsonable_encoder, not raw json.dumps — tool results can contain
        # datetimes (e.g. RoutineOut.model_dump()'s created_at/next_run_at),
        # which json.dumps can't serialize on its own. The REST routes get
        # this for free via FastAPI's response_model pipeline; this is the
        # one place in the app building a JSON string by hand instead of
        # returning a dict through that pipeline, so it needs the same
        # encoder explicitly. Caught by a test with a tool result that
        # actually contains a datetime, not found by inspection.
        await _save(db, household_id, "tool_result", json.dumps(jsonable_encoder(tool_result)), {
            "tool_use_id": body.confirm_tool_call_id,
        })
        response = await _call_model(db, household_id)
        return APIResponse.ok(response)

    if not body.message:
        return APIResponse.fail("VALIDATION_ERROR", "message is required.")

    await _save(db, household_id, "user", body.message)
    response = await _call_model(db, household_id)
    return APIResponse.ok(response)


@router.get("/history", response_model=APIResponse)
async def get_history(request: Request, db: AsyncSession = Depends(get_db)):
    """Full-fidelity turn list for rendering the chat on load — separate
    from the internal Anthropic-format reconstruction _load_history does
    for the model itself."""
    household_id = _household_id(request)
    if not household_id:
        return APIResponse.fail("NOT_AUTHENTICATED", "Not authenticated.")

    from app.models.db import AssistantMessage
    result = await db.execute(
        select(AssistantMessage)
        .where(AssistantMessage.household_id == household_id)
        .order_by(AssistantMessage.created_at.asc())
    )
    rows = result.scalars().all()
    return APIResponse.ok([
        {
            "id": r.id, "role": r.role, "content": r.content,
            "tool_calls": r.tool_calls, "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ])
