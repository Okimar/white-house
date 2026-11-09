"""Chat use cases: create a chat, send a message, regenerate, stop.

Routes call these; they own the order of operations (validate first, then write, then start a run)
so a rejected request never leaves half-created messages behind.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from tavern.context import AppContext
from tavern.core.budget import BudgetExceeded, RunBudget
from tavern.core.assembly import PromptInput
from tavern.core.generation import DefaultGenerator, GenerationJob
from tavern.core.llm import LLMClient
from tavern.core.presets import when_texts
from tavern.core.prompt import DEFAULT_CHAR_NAME, substitute
from tavern.core.runs import Run

from tavern.providers.params import request_params
from tavern.services import cast, character_tree, lorebooks, parallel, speakers, spending
from tavern.services import messages as message_service
from tavern.services import notes as note_service
from tavern.services import presets as preset_service
from tavern.services import regex as regex_service

from tavern.errors import AppError, Conflict, NotFound, TavernError
from tavern.formats.cards import greetings
from tavern.formats.media import media_type, sniff_image
from tavern.storage.repos.chats import ROLES

log = logging.getLogger(__name__)
NARRATOR_JOIN = chr(10) * 2  # several narrator blocks are sent as paragraphs

DEFAULT_USER_NAME = "User"
BUDGET_KEYS = ("max_steps", "max_tokens", "max_cost", "timeout")
# A user message may be the user's own text, so only replies get new versions.
REGENERABLE_ROLES = ("assistant", "system")
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MODEL_IMAGE_TYPES = (".png", ".jpg", ".gif", ".webp")


def load_image(ctx: AppContext, name: str) -> tuple[str, str] | None:
    """A library picture as (media type, base64) for the model; None if gone, too big or not a picture."""
    try:
        blob = ctx.assets.path(name).read_bytes()
    except (NotFound, OSError):
        log.warning("Picture %r of a message is missing; sending the message without it", name)
        return None
    ext = sniff_image(blob)
    if ext not in MODEL_IMAGE_TYPES or len(blob) > MAX_IMAGE_BYTES:  # models do not read SVG
        log.warning("Picture %r is not sent: not a photo-like picture or larger than %d bytes", name, MAX_IMAGE_BYTES)
        return None
    return media_type(ext), base64.b64encode(blob).decode("ascii")
IMPERSONATE_INSTRUCTION = (
    "[Write the next message of {{user}} in the first person, as {{user}} would. "
    "Only {{user}}'s words and actions, no reply from {{char}}.]"
)


def user_name(ctx: AppContext, chat_id: str | None = None) -> str:
    """{{user}} of a chat: its "me" member, else the name from settings."""
    return cast.user_name(ctx, chat_id)


def create_chat(ctx: AppContext, character_id: str | None, title: str | None = None) -> dict[str, Any]:
    """New chat. With a character, its greeting (and alternates as swipes) opens the chat.

    The character becomes the first member; the default "me" character (setting `default_me`)
    joins as the user's own character.
    """
    character = ctx.characters.get(character_id) if character_id else None
    chat = ctx.chats.create_chat(
        character_id=character_id,
        title=(title or "").strip() or (character["name"] if character else "New chat"),
    )
    if character:
        cast.add_member_now(ctx, chat["id"], character["id"])
    default_me = ctx.settings.get("default_me")
    if default_me and default_me != character_id:
        try:
            cast.add_member_now(ctx, chat["id"], default_me, is_me=True)
        except NotFound:
            ctx.settings.delete("default_me")  # that character was deleted
    if character:
        me = user_name(ctx, chat["id"])
        opening = [
            substitute(text, char=character["name"], user=me)
            for text in greetings(character["data"])
        ]
        if opening:
            message = ctx.chats.add_message(
                chat["id"], role="assistant", content=opening[0], name=character["name"],
                character_id=character["id"],
            )
            for alternative in opening[1:]:
                ctx.chats.add_swipe(message["id"], alternative)
            if len(opening) > 1:
                ctx.chats.set_active_swipe(message["id"], 0)
    message_service.announce(ctx, "chat.created", chat_id=chat["id"])
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


def start_send(
    ctx: AppContext, chat_id: str, text: str, reply_role: str = "assistant", images: list[str] | None = None,
    speaker: str | None = None,
) -> Run | None:
    """Append the user's message and an empty reply, then generate the reply in the background.

    `reply_role` is who the generated reply is saved as (the chat's "model writes as" choice).
    `images` are picture names from the library, shown with the message and sent to the model.
    `speaker`: the character who answers ("ответь ты"); default: the chat's order decides
    (services/speakers.py). With two or more characters and the order "manual" only the message is
    saved: None.
    """
    text = text.strip()
    images = list(dict.fromkeys(images or []))
    if not text and not images:
        raise AppError("Message is empty", code="empty_message")
    if len(images) > MAX_IMAGES:
        raise AppError(f"At most {MAX_IMAGES} pictures per message", code="too_many_images")
    for name in images:
        ctx.assets.get(name)  # NotFound for a picture that is not in the library
    _check_role(reply_role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    text, original = regex_service.stored(ctx, chat_id, text, "user") if text else (text, None)
    waits = (reply_role == "assistant" and not speaker and speakers.get_settings(chat)["order"] == "manual"
             and speakers.several(ctx.members.list(chat_id)))
    if waits:  # the user picks who answers
        message = ctx.chats.add_message(
            chat_id, role="user", content=text, parent_id=chat["leaf_id"], name=user_name(ctx, chat_id))
        if images or original:
            ctx.chats.update_swipe_meta(message["id"], 0, {"images": images or None, "regex_before": original})
        message_service.announce(ctx, "message.added", message=message, chat_id=chat_id)
        return None
    connection = resolve_connection(ctx, chat)
    if _round_wanted(ctx, chat, reply_role, speaker):  # everyone answers at once
        warning = spending.guard(ctx)
        user_message = ctx.chats.add_message(
            chat_id, role="user", content=text, parent_id=chat["leaf_id"], name=user_name(ctx, chat_id))
        if images or original:
            ctx.chats.update_swipe_meta(user_message["id"], 0, {"images": images or None, "regex_before": original})
            user_message = ctx.chats.get_message(user_message["id"])
        message_service.announce(ctx, "message.added", message=user_message, chat_id=chat_id)
        return _start_turn(ctx, chat, connection, replies=1, user_message=user_message, warning=warning)
    pending = {"role": "user", "content": text, "name": user_name(ctx, chat_id), "character_id": None}
    speaker, ask = _pick(ctx, chat, reply_role, speaker, [*ctx.chats.active_path(chat_id), pending])
    warning = spending.guard(ctx)

    user_message = ctx.chats.add_message(
        chat_id, role="user", content=text, parent_id=chat["leaf_id"], name=user_name(ctx, chat_id)
    )
    if images or original:
        ctx.chats.update_swipe_meta(user_message["id"], 0, {"images": images or None, "regex_before": original})
        user_message = ctx.chats.get_message(user_message["id"])
    message_service.announce(ctx, "message.added", message=user_message, chat_id=chat_id)
    character_id, name = _author(ctx, chat, reply_role, speaker, ask)
    reply = ctx.chats.add_message(
        chat_id, role=reply_role, content="", parent_id=user_message["id"], name=name, character_id=character_id,
    )
    return _launch(ctx, chat, connection, reply, swipe_idx=0, new_swipe=False,
                   user_message=user_message, instruction=_instruction_for(reply_role), allow_parallel=True,
                   warning=warning, ask_speaker=ask)


def start_continue(ctx: AppContext, chat_id: str, role: str = "assistant", speaker: str | None = None) -> Run:
    """Generate the next message after the current leaf, saved with `role`, without a user turn.

    role="user" is impersonation: the model writes the user's next message. `speaker`: the
    character who writes it ("ответь ты"); default: the chat's order ("manual" acts as natural).
    """
    _check_role(role)
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    connection = resolve_connection(ctx, chat)
    if chat["leaf_id"] is None and role != "user" and not speaker:
        raise Conflict("Nothing to continue", code="nothing_to_continue")
    if _round_wanted(ctx, chat, role, speaker):
        return _start_turn(ctx, chat, connection, replies=1, warning=spending.guard(ctx))
    speaker, ask = _pick(ctx, chat, role, speaker, ctx.chats.active_path(chat_id))
    warning = spending.guard(ctx)
    character_id, name = _author(ctx, chat, role, speaker, ask)
    reply = ctx.chats.add_message(
        chat_id, role=role, content="", parent_id=chat["leaf_id"], name=name, character_id=character_id,
    )
    return _launch(ctx, chat, connection, reply, swipe_idx=0, new_swipe=False,
                   user_message=None, instruction=_instruction_for(role), warning=warning, ask_speaker=ask)


def _round_wanted(ctx: AppContext, chat: dict[str, Any], role: str, speaker: str | None) -> bool:
    """The chat's order is "all", nobody was asked by name and two or more may speak."""
    return (role == "assistant" and not speaker and speakers.get_settings(chat)["order"] == "all"
            and len(speakers.may_speak(ctx.members.list(chat["id"]))) > 1)


def start_talk(ctx: AppContext, chat_id: str, count: int | None = None) -> Run:
    """«Пусть поговорят»: the characters answer one another by the chat's order, without the user,
    until `count` replies (default: the chat's talk_limit), Stop, an error or the spending limit.
    With the order "all" every step is a round of everyone at once."""
    chat = ctx.chats.get_chat(chat_id)
    _ensure_idle(ctx, chat_id)
    connection = resolve_connection(ctx, chat)
    if count is not None and not 1 <= count <= speakers.MAX_TALK:
        raise AppError(f"How many replies: 1..{speakers.MAX_TALK}", code="bad_count")
    if not speakers.may_speak(ctx.members.list(chat_id)):
        raise speakers.nobody()
    warning = spending.guard(ctx)
    count = count or speakers.get_settings(chat)["talk_limit"]
    return _start_turn(ctx, chat, connection, replies=count, warning=warning)


def _pick(
    ctx: AppContext, chat: dict[str, Any], role: str, speaker: str | None, history: list[dict[str, Any]]
) -> tuple[str | None, bool]:
    """Who writes an assistant reply: (character id, None = the chat's main one; ask the model
    first). Raises before anything is written when every character of the chat is muted."""
    if role != "assistant":
        return None, False
    if speaker:
        speakers.require(ctx, chat["id"], speaker)
        return speaker, False
    members = ctx.members.list(chat["id"])
    if not speakers.several(members):
        return None, False
    if not speakers.may_speak(members):
        raise speakers.nobody()
    settings = speakers.get_settings(chat)
    if settings["order"] == "model":
        return None, True
    return speakers.choose(settings, members, history), False


def _author(
    ctx: AppContext, chat: dict[str, Any], role: str, speaker: str | None, ask: bool = False
) -> tuple[str | None, str]:
    """(character_id, name) stored with a new message; empty while the model is still choosing."""
    if role != "assistant":
        return None, _name_for(ctx, chat, role)
    character_id = None if ask else (speaker or chat["character_id"])
    if not character_id:
        return None, ""
    try:
        return character_id, ctx.characters.get(character_id)["name"]
    except NotFound:
        return None, ""


def via_line(label: str, fallback: str, main: str) -> str:
    """The «через …» line of a version. `label`: which parallel request it is ("" = no parallel ones);
    `fallback`: the step that answered for the main connection, if one did: it says nothing about
    a version written by another request."""
    if not label:
        return fallback
    return f"{label} ↪ {fallback}" if fallback and label.startswith(main) else label


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
    character_id, name = _author(ctx, chat, role, None)
    text, original = regex_service.stored(ctx, chat_id, text, role)
    message = ctx.chats.add_message(
        chat_id, role=role, content=text, parent_id=chat["leaf_id"], name=name, character_id=character_id
    )
    if original is not None:
        ctx.chats.update_swipe_meta(message["id"], 0, {"regex_before": original})
        message = ctx.chats.get_message(message["id"])
    message_service.announce(ctx, "message.added", message=message, chat_id=chat_id)
    return message


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
    character_id, name = _author(ctx, chat, role, None)
    return ctx.chats.set_roles(chat_id, {message_id: (role, name, character_id) for message_id in targets})


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise AppError(f"Unknown role {role!r}. Use one of: {', '.join(ROLES)}", code="bad_role")


def _name_for(ctx: AppContext, chat: dict[str, Any], role: str) -> str:
    """Display name stored with a message of this role."""
    if role == "user":
        return user_name(ctx, chat["id"])
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
    warning = spending.guard(ctx)
    before_idx = last["active_swipe"]
    swipe_idx = ctx.chats.add_swipe(last["id"], "")
    last = ctx.chats.get_message(last["id"])
    message_service.announce(ctx, "message.swipe_selected", message=last, chat_id=chat_id, before_idx=before_idx)
    return _launch(ctx, chat, connection, last, swipe_idx=swipe_idx, new_swipe=True,
                   user_message=None, instruction=_instruction_for(last["role"]), allow_parallel=True,
                   warning=warning)


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


def _label(connection: dict[str, Any]) -> str:
    return f"{connection['name']} · {connection['model']}" if connection["model"] else connection["name"]


async def _main_with_variants(
    run: Run, job: GenerationJob, llm: LLMClient, messages: list[Any], extras: list[parallel.Target],
    parts: list[str],
) -> list[parallel.Result]:
    """Stream the main reply as usual while the extra requests run beside it; then wait for them."""
    tasks = [asyncio.create_task(parallel.collect(llm, t, messages, job.params)) for t in extras]
    try:
        async for event in llm.stream(messages, **job.params):
            if event.kind == "text":
                parts.append(event.text)
                run.emit("delta", text=event.text)
            elif event.kind == "reasoning":
                job.think(event.text)
        return list(await asyncio.gather(*tasks))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _add_variants(ctx: AppContext, message_id: str, main_idx: int, results: list[parallel.Result]) -> None:
    """Extra replies become more versions of the message; the main one stays shown."""
    added = False
    for result in results:
        if result.text.strip():
            meta = {"via": result.label, **({"reasoning": result.reasoning} if result.reasoning else {}),
                    **({"usage": result.usage} if result.usage else {})}
            ctx.chats.add_swipe(message_id, result.text, meta)
            added = True
    if added:
        ctx.chats.set_active_swipe(message_id, main_idx)


def _launch(
    ctx: AppContext,
    chat: dict[str, Any],
    connection: dict[str, Any],
    reply: dict[str, Any],
    **options: Any,
) -> Run:
    """Generate `reply` in the background: a run of one reply (see `_writer` for the options)."""
    body, discard = _writer(ctx, chat, connection, reply, **options)
    try:
        return ctx.runs.start(chat["id"], make_budget(ctx), body)
    except BaseException:
        discard()
        raise


Body = Callable[[Run], Awaitable["dict[str, Any] | None"]]


def _writer(
    ctx: AppContext,
    chat: dict[str, Any],
    connection: dict[str, Any],
    reply: dict[str, Any],
    *,
    swipe_idx: int = 0,
    new_swipe: bool = False,
    user_message: dict[str, Any] | None = None,
    instruction: str = "",
    allow_parallel: bool = False,
    warning: str = "",
    ask_speaker: bool = False,
    final: bool = True,
    skip_ids: frozenset[str] = frozenset(),
    keep_empty: bool = False,
) -> tuple[Body, Callable[[], None]]:
    """(body, discard) that write `reply` inside a run. It is written by its character_id (the
    chat's main character when empty); with `ask_speaker` the model first chooses who that is.

    `final`: this reply is the whole run (events start, delta, done | stopped | error). Otherwise
    it is one of several in a turn (stage 7б): start, delta, then reply_done or reply_error; the
    turn ends the run. The body returns the saved message, or None. `skip_ids`: messages left out
    of the history (the other replies of an all-at-once round). `keep_empty`: an empty reply is
    left for the caller to remove (the round re-links its replies first)."""
    chat_id = chat["id"]
    params = dict(connection["params"])
    preset = preset_service.resolve(ctx, chat)
    max_context = int(params.pop("max_context_tokens", 0) or 0)
    # Model settings live only in the connection; the preset's old `params` are not used.
    model_defaults = request_params(connection["kind"], params)
    generator_id = chat["meta"].get("generator") or ctx.settings.get("generator")
    me = user_name(ctx, chat_id)

    def archive(job: GenerationJob) -> None:
        """Keep what the model saw for this reply version ("what the model saw" button)."""
        if job.prompt is None or job.sent is None:
            return  # a plugin generator built its own prompt
        if job.sent == job.prompt.messages:
            entries = [e.as_dict() for e in job.prompt.entries]
        else:  # plugins changed the prompt: keep what was actually sent
            entries = [{"role": m.role, "text": m.content, "source": "", "label": "после плагинов",
                        "kind": "plugin", "msg": i} for i, m in enumerate(job.sent)]
        meta = {
            "preset_id": preset["id"], "preset_name": preset["name"],
            "connection_id": connection["id"], "connection_name": connection["name"],
            "model": connection["model"], "params": model_defaults,
            "tokens": job.prompt.tokens, "dropped": job.prompt.dropped,
            "max_context_tokens": max_context,
        }
        try:
            ctx.prompt_log.record(chat_id, reply["id"], swipe_idx, entries, meta)
        except Exception:  # noqa: BLE001 - the reply itself is saved; the archive is secondary
            log.exception("Could not archive the prompt of message %s", reply["id"])
        try:
            note_service.record_placed(ctx, chat_id, job.prompt.placed)
        except Exception:  # noqa: BLE001 - same: the reply is saved, a note mark is secondary
            log.exception("Could not attach the notes of message %s", reply["id"])

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
            # Regex rules for the saved text (stage 11): once, here; the text before them is kept for «вернуть».
            text, original = regex_service.stored(ctx, chat_id, text, reply["role"])
            ctx.chats.set_content(reply["id"], text, swipe_idx=swipe_idx)
            if original is not None:
                ctx.chats.update_swipe_meta(reply["id"], swipe_idx, {"regex_before": original})
            return True
        if not keep_empty:
            discard()
        return False

    def report_failure(error: dict[str, Any]) -> None:
        if final:
            run_ref[0].emit("error", error=error, message=snapshot())
        else:
            run_ref[0].emit("reply_error", id=reply["id"], error=error)

    run_ref: list[Run] = []

    async def body(run: Run) -> dict[str, Any] | None:
        run_ref[:] = [run]
        nonlocal reply
        provider = ctx.providers.create(connection)
        parts: list[str] = []
        job: GenerationJob | None = None
        via_label = ""
        extras: list[parallel.Target] = []
        tally = spending.Tally()  # what the shown version cost (the race winner's in "first" mode)
        tallies: dict[str, spending.Tally] = {}

        def finish(text: str) -> bool:
            saved = settle(text)
            if saved and job is not None:
                extra_meta: dict[str, Any] = dict(job.meta)  # a plugin writer's own data first: core keys win
                if job.reasoning:
                    extra_meta["reasoning"] = "".join(job.reasoning)
                via = via_line(via_label, getattr(provider, "used", ""), _label(connection))
                if via:
                    extra_meta["via"] = via
                spent = tallies.get(via_label, tally)
                if spent.requests:
                    extra_meta["usage"] = spent.as_meta()
                if extra_meta:
                    ctx.chats.update_swipe_meta(reply["id"], swipe_idx, extra_meta)
                archive(job)
            return saved

        try:
            llm = LLMClient(provider, model=connection["model"], budget=run.budget, trace=run.trace,
                            defaults=model_defaults,
                            meter=spending.meter(ctx, connection, chat_id=chat_id, message_id=reply["id"],
                                                 tally=tally))
            history = [
                m for m in ctx.chats.active_path(chat_id) if m["id"] != reply["id"] and m["id"] not in skip_ids
            ]
            current = ctx.chats.get_chat(chat_id)
            rows = ctx.members.list(chat_id)
            settings = speakers.get_settings(current)
            if ask_speaker:
                chosen_id = None
                try:
                    chosen_id = await speakers.ask_model(
                        llm.fork(meter=spending.meter(ctx, connection, chat_id=chat_id, message_id=reply["id"],
                                                      purpose="speaker", tally=tally)), rows, history)
                except TavernError as exc:  # not fatal: the natural order decides instead
                    run.trace("speaker_choice_failed", error=str(exc))
                chosen_id = chosen_id or speakers.choose(settings, rows, history)
                name = next((m["name"] for m in rows if m["character_id"] == chosen_id), "")
                ctx.chats.set_speaker(reply["id"], chosen_id, name)
                reply = snapshot() or reply
            character = None
            speaker = reply.get("character_id") if reply["role"] == "assistant" else None
            for character_id in (speaker, current["character_id"]):
                try:
                    character = ctx.characters.get(character_id) if character_id else None
                except NotFound:
                    character = None
                if character:
                    break
            members = cast.prompt_members(ctx, current, character["id"] if character else None)
            # A plugin (the memory) may need the query vectors even when no bubble has a vector: it says so with the
            # filter event, and still ONE embedding request serves bubbles, lorebooks and the plugin.
            wanted = bool(await ctx.events.filter("meaning.wanted", False, chat=chat))
            found_meaning = await lorebooks.meaning_for_reply(ctx, chat, members, history, wanted=wanted)
            meaning, meaning_error = found_meaning.scores, found_meaning.error
            chosen = await character_tree.select_for_chat(ctx, chat, members, history, meaning=meaning)
            for member in members:
                member.blocks = chosen.get(member.character_id, [])
            lore = await lorebooks.select_for_chat(
                ctx, chat, members, history, max_context=max_context,
                seed_text="\n".join(f.block["content"] for found in chosen.values() for f in found),
                meaning=meaning)
            # Rules "for the model" (stage 11): in a thread, because the worker is waited for.
            rewritten, lore_rewritten, thoughts_rewritten, skipped = await asyncio.to_thread(
                regex_service.prompt_texts, ctx, chat, history, lore.found,
                character["name"] if character else DEFAULT_CHAR_NAME, me)
            regex_service.announce(ctx, skipped)
            prompt_input = PromptInput(
                preset=preset["data"], preset_id=preset["id"], rewritten=rewritten, lore_rewritten=lore_rewritten,
                thoughts_rewritten=thoughts_rewritten,
                members=members,
                history=note_service.with_attached(ctx, chat_id, history), user_name=me,
                impersonate=reply["role"] == "user",
                max_context_tokens=max_context,
                notes=note_service.for_reply(ctx, chat_id, [m.character_id for m in members], history),
                lore=lore.found, lore_dropped=lore.dropped,
                load_image=lambda name: load_image(ctx, name),
                several=speakers.several(rows),
                scene=speakers.scene(rows, character["id"] if character else None, settings["others"])
                if speakers.several(rows) else [],
            )
            job = GenerationJob(ctx=ctx, chat=chat, card=character["data"] if character else None,
                                history=history, user_name=me, llm=llm, run=run,
                                max_context_tokens=max_context, instruction=instruction,
                                prompt_input=prompt_input, message_id=reply["id"])
            job.meaning = found_meaning
            job.params["split_thinking"] = True  # <thinking> written in the reply goes to «Размышление»
            job.on_reasoning = lambda text: run.emit("reasoning", id=reply["id"], text=text)
            run.emit("start", run_id=run.id, message=reply, user_message=user_message)
            if warning:
                run.emit("spend_warning", text=warning)
            if meaning_error:  # the reply goes on by keywords; the page shows why meaning search did not work
                run.emit("meaning_failed", text=meaning_error)
            allow = allow_parallel and final
            await ctx.events.emit("chat.before_generate", job=job)
            generator = ctx.generators.get(generator_id)
            owner = ctx.generators.owner_of(generator.id)
            if owner:  # a plugin writes this reply: its requests obey its access rules and count as its own
                job.llm = llm.fork(meter=spending.meter(ctx, connection, chat_id=chat_id, message_id=reply["id"],
                                                        purpose=f"plugin:{owner}", tally=tally),
                                   gate=ctx.plugins.gate_for(owner, chat_id=chat_id))
            plan = parallel.get_settings(ctx)
            results: list[parallel.Result] = []
            if allow and plan["enabled"] and isinstance(generator, DefaultGenerator):
                extras = parallel.extra_targets(ctx, connection, plan)
                for target in extras:
                    tallies[target.label] = spending.Tally()
                    target.meter = spending.meter(ctx, target.connection or connection, chat_id=chat_id,
                                                  message_id=reply["id"], tally=tallies[target.label])
            if not extras:
                async for delta in generator.generate(job):
                    parts.append(delta)
                    run.emit("delta", id=reply["id"], text=delta)
            else:
                messages = await job.build_messages()
                if plan["mode"] != "first":
                    via_label = _label(connection) + " (основной)"
                    results = await _main_with_variants(run, job, llm, messages, extras, parts)
                else:
                    tallies[_label(connection)] = tally
                    streams = [(_label(connection), llm.stream(messages, **job.params))]
                    streams += [(t.label, parallel.client(llm, t).stream(messages, **job.params)) for t in extras]
                    async for label, event in parallel.race(streams):
                        if event.kind == "text":
                            parts.append(event.text)
                            run.emit("delta", id=reply["id"], text=event.text)
                        elif event.kind == "reasoning":
                            job.think(event.text)
                        via_label = label
            text = "".join(parts)
            for result in results:
                if result.label in tallies and tallies[result.label].requests:
                    result.usage = tallies[result.label].as_meta()
            if results and text.strip():
                _add_variants(ctx, reply["id"], swipe_idx, results)
                failed = [{"label": r.label, "error": r.error} for r in results if r.error]
                if failed:
                    run.emit("variants_failed", failed=failed)
            if not finish(text):
                report_failure({"code": "empty_reply", "message": "The model returned an empty reply"})
                return None
            message = snapshot()
            if final:
                run.emit("done", message=message, budget=run.budget.snapshot(),
                         pick=bool(results) and plan["mode"] == "pick")
            else:
                run.emit("reply_done", message=message)
            await ctx.events.emit("message.received", message=message, chat=chat)
            return message
        except asyncio.CancelledError:
            finish("".join(parts))
            if final:
                run.emit("stopped", message=snapshot())
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the client below
            if not isinstance(exc, TavernError):
                log.exception("Generation failed in chat %s", chat_id)
            saved = finish("".join(parts))
            report_failure(_error_dict(exc))
            await ctx.events.emit("generation.failed", chat_id=chat_id, error=exc)
            return snapshot() if saved and not final else None
        finally:
            await provider.aclose()
            for target in extras:
                await target.provider.aclose()

    return body, discard


# --- turns of several replies (stage 7б) -----------------------------------------------------


def _start_turn(
    ctx: AppContext, chat: dict[str, Any], connection: dict[str, Any], *, replies: int,
    user_message: dict[str, Any] | None = None, warning: str = "",
) -> Run:
    """A run that writes several replies: one after another by the chat's order, or rounds of
    everyone at once (order "all"), until `replies` are written. Events: turn_start
    {user_message}, then per reply start / delta {id} / reply_done | reply_error, then
    done | stopped | error with reload (the client reloads the newest page: a round may reorder)."""
    chat_id = chat["id"]

    async def body(run: Run) -> None:
        run.emit("turn_start", run_id=run.id, user_message=user_message)
        if warning:
            run.emit("spend_warning", text=warning)
        written: list[dict[str, Any]] = []
        attempts = 0  # replies of characters (the narrator's scene does not count)
        warned = {warning}
        try:
            while attempts < replies:
                current = ctx.chats.get_chat(chat_id)
                rows = ctx.members.list(chat_id)
                settings = speakers.get_settings(current)
                if not speakers.may_speak(rows):
                    break
                if settings["order"] == "all" and len(speakers.may_speak(rows)) > 1:
                    got = await _round(ctx, run, current, connection, rows, settings)
                else:
                    got = await _one(ctx, run, current, connection, rows, settings)
                written += got
                attempts += max(1, sum(m["role"] == "assistant" for m in got))
                if not got or attempts >= replies:
                    break
                more = spending.guard(ctx)  # the stop setting raises: the turn ends with an error
                if more not in warned:
                    run.emit("spend_warning", text=more)
                    warned.add(more)
            run.emit("done", messages=written, reload=True, budget=run.budget.snapshot())
        except asyncio.CancelledError:
            run.emit("stopped", reload=True)
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the client
            if not isinstance(exc, TavernError):
                log.exception("A turn failed in chat %s", chat_id)
            run.emit("error", error=_error_dict(exc), reload=True)

    return ctx.runs.start(chat_id, make_budget(ctx), body)


async def _one(
    ctx: AppContext, run: Run, chat: dict[str, Any], connection: dict[str, Any], rows: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """The next reply by the chat's order ("manual" acts as natural)."""
    ask = settings["order"] == "model" and len(speakers.may_speak(rows)) > 1
    speaker = None if ask else speakers.choose(settings, rows, ctx.chats.active_path(chat["id"]))
    character_id, name = _author(ctx, chat, "assistant", speaker, ask)
    reply = ctx.chats.add_message(chat["id"], role="assistant", content="", parent_id=chat["leaf_id"],
                                  name=name, character_id=character_id)
    body, _ = _writer(ctx, chat, connection, reply, final=False, ask_speaker=ask)
    message = await body(run)
    return [message] if message else []


async def _round(
    ctx: AppContext, run: Run, chat: dict[str, Any], connection: dict[str, Any], rows: list[dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Everyone who may speak answers at once, each seeing the chat up to the round. Then the
    replies are put in order (or joined into one message) and the narrator may sum them up."""
    parent = chat["leaf_id"]
    replies: list[dict[str, Any]] = []
    for member in speakers.may_speak(rows):
        replies.append(ctx.chats.add_message(
            chat["id"], role="assistant", content="", parent_id=replies[-1]["id"] if replies else parent,
            name=member["name"], character_id=member["character_id"]))
    skip = frozenset(r["id"] for r in replies)
    finished: list[str] = []

    async def write(reply: dict[str, Any]) -> None:
        body, _ = _writer(ctx, chat, connection, reply, final=False, skip_ids=skip, keep_empty=True)
        if await body(run):
            finished.append(reply["id"])

    tasks = [asyncio.create_task(write(r)) for r in replies]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        written = _arrange(ctx, chat["id"], parent, [r["id"] for r in replies], finished, settings["together"])
    if written and settings["narrator"]["enabled"]:
        told = await _narrate(ctx, run, ctx.chats.get_chat(chat["id"]), connection, rows, settings, written)
        if told:
            written.append(told)
    return written


def _arrange(
    ctx: AppContext, chat_id: str, parent: str | None, ids: list[str], finished: list[str], together: str,
) -> list[dict[str, Any]]:
    """Order the replies of a round ("finished" / "list") or join them ("one"); drop empty ones."""
    by_id = {i: ctx.chats.get_message(i) for i in ids}
    kept = [i for i in ids if by_id[i]["content"].strip()]
    order = [i for i in finished if i in kept] + [i for i in kept if i not in finished] if together == "finished" else kept
    drop = [i for i in ids if i not in kept]
    if together == "one" and len(order) > 1:
        first = order[0]
        ctx.chats.set_content(first, "\n\n".join(f"{by_id[i]['name']}: {by_id[i]['content']}" for i in order))
        ctx.chats.set_speaker(first, None, ", ".join(by_id[i]["name"] for i in order))
        ctx.chats.set_extra(first, {"together": True})
        usage = _sum_usage([by_id[i]["swipe_meta"].get("usage") for i in order])
        if usage:
            ctx.chats.update_swipe_meta(first, 0, {"usage": usage, "reasoning": ""})
        drop += order[1:]
        order = [first]
    ctx.chats.rechain(chat_id, parent, order, drop)
    return [ctx.chats.get_message(i) for i in order]


def _sum_usage(parts: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    found = [u for u in parts if u]
    if not found:
        return None
    costs = [u["cost"] for u in found if u.get("cost") is not None]
    return {"prompt_tokens": sum(u["prompt_tokens"] for u in found),
            "completion_tokens": sum(u["completion_tokens"] for u in found),
            "cost": round(sum(costs), 8) if costs else None,
            "unpriced": any(u.get("unpriced") for u in found), "estimated": any(u.get("estimated") for u in found),
            "requests": sum(u.get("requests", 0) for u in found)}


async def _narrate(
    ctx: AppContext, run: Run, chat: dict[str, Any], connection: dict[str, Any], rows: list[dict[str, Any]],
    settings: dict[str, Any], round_: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The narrator turns the round into one scene: a system message under its name, written by
    its own connection and model when set (else the chat's)."""
    narrator = settings["narrator"]
    target = connection
    if narrator["connection_id"]:
        try:
            target = ctx.connections.get(narrator["connection_id"])
        except NotFound:
            run.emit("spend_warning", text="Подключение рассказчика удалено: он пишет через подключение чата")
    model = narrator["model"] or target["model"]
    round_ids = {m["id"] for m in round_}
    history = [m for m in ctx.chats.active_path(chat["id"]) if m["id"] not in round_ids]
    preset = preset_service.resolve(ctx, chat)
    instruction = NARRATOR_JOIN.join(
        substitute(b["text"], char=narrator["name"], user=user_name(ctx, chat["id"]))
        for b in when_texts(preset["data"], "narrator"))
    prompt = speakers.narrator_prompt(instruction, rows, history, round_)
    message = ctx.chats.add_message(chat["id"], role="system", content="", parent_id=chat["leaf_id"],
                                    name=narrator["name"])
    params = dict(target["params"])
    params.pop("max_context_tokens", None)
    tally = spending.Tally()
    provider = ctx.providers.create(target)
    llm = LLMClient(provider, model=model, budget=run.budget, trace=run.trace,
                    defaults=request_params(target["kind"], params),
                    meter=spending.meter(ctx, target, chat_id=chat["id"], message_id=message["id"],
                                         purpose="narrator", tally=tally))
    run.emit("start", run_id=run.id, message=message, user_message=None)
    parts: list[str] = []
    thinking: list[str] = []

    def save() -> bool:
        text = "".join(parts)
        if not text.strip():
            ctx.chats.delete_message(message["id"])
            return False
        ctx.chats.set_content(message["id"], text)
        meta: dict[str, Any] = {"usage": tally.as_meta()} if tally.requests else {}
        if thinking:
            meta["reasoning"] = "".join(thinking)
        if meta:
            ctx.chats.update_swipe_meta(message["id"], 0, meta)
        try:
            entries = [{"role": m.role, "text": m.content, "source": "prompt:narrator", "label": narrator["name"],
                        "kind": "prompt", "msg": i} for i, m in enumerate(prompt)]
            ctx.prompt_log.record(chat["id"], message["id"], 0, entries, {
                "preset_id": preset["id"], "preset_name": preset["name"], "connection_id": target["id"],
                "connection_name": target["name"], "model": model, "params": {}, "tokens": 0, "dropped": {},
                "max_context_tokens": 0})
        except Exception:  # noqa: BLE001 - the scene itself is saved; the archive is secondary
            log.exception("Could not archive the narrator's prompt")
        return True

    try:
        async for event in llm.stream(prompt, split_thinking=True):
            if event.kind == "text":
                parts.append(event.text)
                run.emit("delta", id=message["id"], text=event.text)
            elif event.kind == "reasoning":
                thinking.append(event.text)
                run.emit("reasoning", id=message["id"], text=event.text)
    except asyncio.CancelledError:
        save()
        raise
    except Exception as exc:  # noqa: BLE001 - the round is kept; only the scene failed
        if not isinstance(exc, TavernError):
            log.exception("The narrator failed in chat %s", chat["id"])
        save()
        run.emit("reply_error", id=message["id"], error=_error_dict(exc))
        return None
    finally:
        await provider.aclose()
    if not save():
        run.emit("reply_error", id=message["id"], error={"code": "empty_reply", "message": "Рассказчик ничего не написал"})
        return None
    told = ctx.chats.get_message(message["id"])
    run.emit("reply_done", message=told)
    return told
