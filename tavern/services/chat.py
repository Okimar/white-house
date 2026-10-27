"""Chat use cases: create a chat, send a message, regenerate, stop.

Routes call these; they own the order of operations (validate first, then write, then start a run)
so a rejected request never leaves half-created messages behind.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from tavern.context import AppContext
from tavern.core.budget import BudgetExceeded, RunBudget
from tavern.core.generation import GenerationJob
from tavern.core.llm import LLMClient
from tavern.core.prompt import substitute
from tavern.core.runs import Run
from tavern.errors import AppError, Conflict, NotFound, TavernError
from tavern.formats.cards import greetings
from tavern.storage.repos.chats import ROLES

log = logging.getLogger(__name__)

DEFAULT_USER_NAME = "User"
BUDGET_KEYS = ("max_steps", "max_tokens", "max_cost", "timeout")
MODEL_PARAM_KEYS = ("temperature", "top_p", "max_tokens", "stop")
# A user message may be the user's own text, so only replies get new versions.
REGENERABLE_ROLES = ("assistant", "system")
IMPERSONATE_INSTRUCTION = (
    "[Write the next message of {{user}} in the first person, as {{user}} would. "
    "Only {{user}}'s words and actions, no reply from {{char}}.]"
)


def user_name(ctx: AppContext) -> str:
    return ctx.settings.get("user_name") or DEFAULT_USER_NAME


def create_chat(ctx: AppContext, character_id: str | None, title: str | None = None) -> dict[str, Any]:
    """New chat. With a character, its greeting (and alternates as swipes) opens the chat."""
    character = ctx.characters.get(character_id) if character_id else None
    chat = ctx.chats.create_chat(
        character_id=character_id,
        title=(title or "").strip() or (character["name"] if character else "New chat"),
    )
    if character:
        me = user_name(ctx)
        opening = [
            substitute(text, char=character["name"], user=me)
            for text in greetings(character["data"])
        ]
        if opening:
            message = ctx.chats.add_message(
                chat["id"], role="assistant", content=opening[0], name=character["name"]
            )
            for alternative in opening[1:]:
                ctx.chats.add_swipe(message["id"], alternative)
            if len(opening) > 1:
                ctx.chats.set_active_swipe(message["id"], 0)
    return ctx.chats.get_chat(chat["id"])


def resolve_connection(ctx: AppContext, chat: dict[str, Any]) -> dict[str, Any]:
    connection_id = chat["meta"].get("connection_id") or ctx.settings.get("active_connection")
    if not connection_id:
        raise Conflict("No API connection selected. Add one in Settings.", code="no_connection")
    try:
        return ctx.connections.get(connection_id)
    except NotFound:
        raise Conflict(
            "The selected API connection no longer exists. Pick another in Settings.",
            code="no_connection",
        ) from None


def make_budget(ctx: AppContext) -> RunBudget:
    configured = ctx.settings.get("run_budget") or {}
    return RunBudget(**{k: configured[k] for k in BUDGET_KEYS if k in configured})


def start_send(ctx: AppContext, chat_id: str, text: str, reply_role: str = "assistant") -> Run:
    """Append the user's message and an empty reply, then generate the reply in the background.

    `reply_role` is who the generated reply is saved as (the chat's "model writes as" choice).
    """
    text = text.strip()
    if not text:
        raise AppError("Message is empty", code="empty_message")
    _check_role(reply_role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    connection = resolve_connection(ctx, chat)
    character = ctx.characters.get(chat["character_id"]) if chat["character_id"] else None

    user_message = ctx.chats.add_message(
        chat_id, role="user", content=text, parent_id=chat["leaf_id"], name=user_name(ctx)
    )
    reply = ctx.chats.add_message(
        chat_id, role=reply_role, content="", parent_id=user_message["id"],
        name=_name_for(ctx, chat, reply_role),
    )
    return _launch(ctx, chat, connection, character, reply, swipe_idx=0, new_swipe=False,
                   user_message=user_message, instruction=_instruction_for(reply_role))


def start_continue(ctx: AppContext, chat_id: str, role: str = "assistant") -> Run:
    """Generate the next message after the current leaf, saved with `role`, without a user turn.

    role="user" is impersonation: the model writes the user's next message.
    """
    _check_role(role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    connection = resolve_connection(ctx, chat)
    character = ctx.characters.get(chat["character_id"]) if chat["character_id"] else None
    if chat["leaf_id"] is None and role != "user":
        raise Conflict("Nothing to continue", code="nothing_to_continue")
    reply = ctx.chats.add_message(
        chat_id, role=role, content="", parent_id=chat["leaf_id"], name=_name_for(ctx, chat, role),
    )
    return _launch(ctx, chat, connection, character, reply, swipe_idx=0, new_swipe=False,
                   user_message=None, instruction=_instruction_for(role))


def start_impersonate(ctx: AppContext, chat_id: str) -> Run:
    """Let the model write the user's next turn (kept for API clients; same as continue as user)."""
    return start_continue(ctx, chat_id, role="user")


def _instruction_for(role: str) -> str:
    """Extra final user turn the model needs to write as `role`; assistant and system need none."""
    return IMPERSONATE_INSTRUCTION if role == "user" else ""


def add_message(ctx: AppContext, chat_id: str, text: str, role: str) -> dict[str, Any]:
    """Save a message with the chosen role as is, without generating a reply."""
    text = text.strip()
    if not text:
        raise AppError("Message is empty", code="empty_message")
    _check_role(role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    return ctx.chats.add_message(
        chat_id, role=role, content=text, parent_id=chat["leaf_id"], name=_name_for(ctx, chat, role)
    )


def parse_numbers(spec: str, limit: int) -> set[int]:
    """'1-5, 8' -> {1, 2, 3, 4, 5, 8}. Numbers are 1-based positions on the active branch."""
    result: set[int] = set()
    for part in re.split(r"[,;\s]+", re.sub(r"\s*-\s*", "-", spec.strip())):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match is None:
            raise AppError(f"Cannot read message numbers {part!r}; write e.g. 1-5, 8", code="bad_numbers")
        low, high = sorted((int(match[1]), int(match[2] or match[1])))
        if low < 1 or high > limit:
            raise AppError(f"Message number {part} is outside 1..{limit}", code="bad_numbers")
        result.update(range(low, high + 1))
    return result


def set_roles(
    ctx: AppContext,
    chat_id: str,
    role: str,
    *,
    ids: list[str] | None = None,
    numbers: str = "",
    everything: bool = False,
) -> list[dict[str, Any]]:
    """Change who a message is from: picked ids, numbers on the active branch, or the whole branch."""
    _check_role(role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    path = ctx.chats.active_path(chat_id)
    if everything:
        targets = [m["id"] for m in path]
    else:
        wanted = parse_numbers(numbers, ctx.chats.path_length(chat_id)) if numbers.strip() else set()
        targets = [m["id"] for m in path if m["depth"] + 1 in wanted]
        targets += [i for i in dict.fromkeys(ids or []) if i not in targets]
    if not targets:
        raise AppError("No messages selected", code="nothing_selected")
    name = _name_for(ctx, chat, role)
    return ctx.chats.set_roles(chat_id, {message_id: (role, name) for message_id in targets})


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise AppError(f"Unknown role {role!r}. Use one of: {', '.join(ROLES)}", code="bad_role")


def _name_for(ctx: AppContext, chat: dict[str, Any], role: str) -> str:
    """Display name stored with a message of this role."""
    if role == "user":
        return user_name(ctx)
    if role == "assistant" and chat["character_id"]:
        try:
            return ctx.characters.get(chat["character_id"])["name"]
        except NotFound:
            return ""
    return ""


def start_regenerate(ctx: AppContext, chat_id: str) -> Run:
    """Write another version (swipe) of the last reply (an assistant or system message)."""
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    if chat["leaf_id"] is None:
        raise Conflict("Nothing to regenerate", code="nothing_to_regenerate")
    last = ctx.chats.get_message(chat["leaf_id"])
    if last["role"] not in REGENERABLE_ROLES:
        raise Conflict("The last message is not a reply", code="nothing_to_regenerate")
    connection = resolve_connection(ctx, chat)
    character = ctx.characters.get(chat["character_id"]) if chat["character_id"] else None
    swipe_idx = ctx.chats.add_swipe(last["id"], "")
    last = ctx.chats.get_message(last["id"])
    return _launch(ctx, chat, connection, character, last, swipe_idx=swipe_idx, new_swipe=True,
                   user_message=None, instruction=_instruction_for(last["role"]))


def stop(ctx: AppContext, chat_id: str) -> bool:
    return ctx.runs.stop(chat_id)


def _ensure_idle(ctx: AppContext, chat_id: str) -> None:
    if ctx.runs.active(chat_id) is not None:
        raise Conflict("A generation is already running in this chat", code="busy")


def _error_dict(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, BudgetExceeded):
        return {"code": "budget_exceeded", "message": str(exc), "details": {"limit": exc.limit}}
    if isinstance(exc, AppError):
        return exc.to_dict()["error"]
    if isinstance(exc, TavernError):
        return {"code": "error", "message": str(exc)}
    return {"code": "internal", "message": "Generation failed unexpectedly, see data/logs"}


def _launch(
    ctx: AppContext,
    chat: dict[str, Any],
    connection: dict[str, Any],
    character: dict[str, Any] | None,
    reply: dict[str, Any],
    *,
    swipe_idx: int,
    new_swipe: bool,
    user_message: dict[str, Any] | None,
    instruction: str = "",
) -> Run:
    chat_id = chat["id"]
    params = dict(connection["params"])
    max_context = int(params.pop("max_context_tokens", 0) or 0)
    model_defaults = {k: params[k] for k in MODEL_PARAM_KEYS if k in params}
    generator_id = chat["meta"].get("generator") or ctx.settings.get("generator")
    me = user_name(ctx)

    def discard() -> None:
        """Remove the empty placeholder so a failed or stopped generation leaves no blank reply."""
        if new_swipe:
            ctx.chats.remove_last_swipe(reply["id"])
        else:
            ctx.chats.delete_message(reply["id"])

    def snapshot() -> dict[str, Any] | None:
        try:
            return ctx.chats.get_message(reply["id"])
        except NotFound:
            return None

    def settle(text: str) -> bool:
        """Persist what was written. Returns False when the text was empty and got discarded."""
        if text.strip():
            ctx.chats.set_content(reply["id"], text, swipe_idx=swipe_idx)
            return True
        discard()
        return False

    async def body(run: Run) -> None:
        provider = ctx.providers.create(connection)
        parts: list[str] = []
        try:
            llm = LLMClient(provider, model=connection["model"], budget=run.budget,
                            trace=run.trace, defaults=model_defaults)
            history = [
                m for m in ctx.chats.active_path(chat_id) if m["id"] != reply["id"]
            ]
            job = GenerationJob(ctx=ctx, chat=chat, card=character["data"] if character else None,
                                history=history, user_name=me, llm=llm, run=run,
                                max_context_tokens=max_context, instruction=instruction)
            run.emit("start", run_id=run.id, message=reply, user_message=user_message)
            await ctx.events.emit("chat.before_generate", job=job)
            async for delta in ctx.generators.get(generator_id).generate(job):
                parts.append(delta)
                run.emit("delta", text=delta)
            text = "".join(parts)
            if not settle(text):
                run.emit("error", error={"code": "empty_reply",
                                         "message": "The model returned an empty reply"},
                         message=snapshot())
                return
            message = snapshot()
            run.emit("done", message=message, budget=run.budget.snapshot())
            await ctx.events.emit("message.received", message=message, chat=chat)
        except asyncio.CancelledError:
            settle("".join(parts))
            run.emit("stopped", message=snapshot())
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the client below
            if not isinstance(exc, TavernError):
                log.exception("Generation failed in chat %s", chat_id)
            settle("".join(parts))
            run.emit("error", error=_error_dict(exc), message=snapshot())
            await ctx.events.emit("generation.failed", chat_id=chat_id, error=exc)
        finally:
            await provider.aclose()

    try:
        return ctx.runs.start(chat_id, make_budget(ctx), body)
    except BaseException:
        discard()
        raise
