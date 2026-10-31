"""Chat use cases: create a chat, send a message, regenerate, stop.

Routes call these; they own the order of operations (validate first, then write, then start a run)
so a rejected request never leaves half-created messages behind.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

from tavern.context import AppContext
from tavern.core.budget import BudgetExceeded, RunBudget
from tavern.core.assembly import PromptInput
from tavern.core.generation import DefaultGenerator, GenerationJob
from tavern.core.llm import LLMClient
from tavern.core.prompt import substitute
from tavern.core.runs import Run
from tavern.providers.params import request_params
from tavern.services import cast, character_tree, lorebooks, parallel, spending
from tavern.services import notes as note_service
from tavern.services import presets as preset_service
from tavern.errors import AppError, Conflict, NotFound, TavernError
from tavern.formats.cards import greetings
from tavern.formats.media import media_type, sniff_image
from tavern.storage.repos.chats import ROLES

log = logging.getLogger(__name__)

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


def start_send(
    ctx: AppContext, chat_id: str, text: str, reply_role: str = "assistant", images: list[str] | None = None
) -> Run:
    """Append the user's message and an empty reply, then generate the reply in the background.

    `reply_role` is who the generated reply is saved as (the chat's "model writes as" choice).
    `images` are picture names from the library, shown with the message and sent to the model.
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
    connection = resolve_connection(ctx, chat)
    warning = spending.guard(ctx)
    character = ctx.characters.get(chat["character_id"]) if chat["character_id"] else None

    user_message = ctx.chats.add_message(
        chat_id, role="user", content=text, parent_id=chat["leaf_id"], name=user_name(ctx, chat_id)
    )
    if images:
        ctx.chats.update_swipe_meta(user_message["id"], 0, {"images": images})
        user_message = ctx.chats.get_message(user_message["id"])
    reply = ctx.chats.add_message(
        chat_id, role=reply_role, content="", parent_id=user_message["id"],
        name=_name_for(ctx, chat, reply_role),
    )
    return _launch(ctx, chat, connection, character, reply, swipe_idx=0, new_swipe=False,
                   user_message=user_message, instruction=_instruction_for(reply_role), allow_parallel=True,
                   warning=warning)


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
    warning = spending.guard(ctx)
    reply = ctx.chats.add_message(
        chat_id, role=role, content="", parent_id=chat["leaf_id"], name=_name_for(ctx, chat, role),
    )
    return _launch(ctx, chat, connection, character, reply, swipe_idx=0, new_swipe=False,
                   user_message=None, instruction=_instruction_for(role), warning=warning)


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
    character = ctx.characters.get(chat["character_id"]) if chat["character_id"] else None
    warning = spending.guard(ctx)
    swipe_idx = ctx.chats.add_swipe(last["id"], "")
    last = ctx.chats.get_message(last["id"])
    return _launch(ctx, chat, connection, character, last, swipe_idx=swipe_idx, new_swipe=True,
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
    character: dict[str, Any] | None,
    reply: dict[str, Any],
    *,
    swipe_idx: int,
    new_swipe: bool,
    user_message: dict[str, Any] | None,
    instruction: str = "",
    allow_parallel: bool = False,
    warning: str = "",
) -> Run:
    chat_id = chat["id"]
    params = dict(connection["params"])
    preset = preset_service.resolve(ctx, chat)
    max_context = preset["data"]["max_context_tokens"] or int(params.pop("max_context_tokens", 0) or 0)
    # The preset's model settings win over the connection's.
    model_defaults = request_params(connection["kind"], params)
    model_defaults.update(preset["data"]["params"])
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
            ctx.chats.set_content(reply["id"], text, swipe_idx=swipe_idx)
            return True
        discard()
        return False

    async def body(run: Run) -> None:
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
                extra_meta: dict[str, Any] = {}
                if job.reasoning:
                    extra_meta["reasoning"] = "".join(job.reasoning)
                if getattr(provider, "used", ""):
                    extra_meta["via"] = provider.used  # a fallback step answered instead
                if via_label:
                    extra_meta["via"] = via_label  # parallel requests: which one this version is
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
                m for m in ctx.chats.active_path(chat_id) if m["id"] != reply["id"]
            ]
            members = cast.prompt_members(ctx, ctx.chats.get_chat(chat_id))
            chosen = await character_tree.select_for_chat(ctx, chat, members, history)
            for member in members:
                member.blocks = chosen.get(member.character_id, [])
            lore = await lorebooks.select_for_chat(
                ctx, chat, members, history, max_context=max_context,
                seed_text="\n".join(f.block["content"] for found in chosen.values() for f in found))
            prompt_input = PromptInput(
                preset=preset["data"], preset_id=preset["id"],
                members=members,
                history=note_service.with_attached(ctx, chat_id, history), user_name=me,
                impersonate=reply["role"] == "user",
                max_context_tokens=max_context,
                notes=note_service.for_reply(ctx, chat_id, [m.character_id for m in members], history),
                lore=lore.found, lore_dropped=lore.dropped,
                load_image=lambda name: load_image(ctx, name),
            )
            job = GenerationJob(ctx=ctx, chat=chat, card=character["data"] if character else None,
                                history=history, user_name=me, llm=llm, run=run,
                                max_context_tokens=max_context, instruction=instruction,
                                prompt_input=prompt_input)
            job.on_reasoning = lambda text: run.emit("reasoning", text=text)
            run.emit("start", run_id=run.id, message=reply, user_message=user_message)
            if warning:
                run.emit("spend_warning", text=warning)
            await ctx.events.emit("chat.before_generate", job=job)
            generator = ctx.generators.get(generator_id)
            plan = parallel.get_settings(ctx)
            results: list[parallel.Result] = []
            if allow_parallel and plan["enabled"] and isinstance(generator, DefaultGenerator):
                extras = parallel.extra_targets(ctx, connection, plan, overrides=preset["data"]["params"])
                for target in extras:
                    tallies[target.label] = spending.Tally()
                    target.meter = spending.meter(ctx, target.connection or connection, chat_id=chat_id,
                                                  message_id=reply["id"], tally=tallies[target.label])
            if not extras:
                async for delta in generator.generate(job):
                    parts.append(delta)
                    run.emit("delta", text=delta)
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
                            run.emit("delta", text=event.text)
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
                run.emit("error", error={"code": "empty_reply",
                                         "message": "The model returned an empty reply"},
                         message=snapshot())
                return
            message = snapshot()
            run.emit("done", message=message, budget=run.budget.snapshot(),
                     pick=bool(results) and plan["mode"] == "pick")
            await ctx.events.emit("message.received", message=message, chat=chat)
        except asyncio.CancelledError:
            finish("".join(parts))
            run.emit("stopped", message=snapshot())
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the client below
            if not isinstance(exc, TavernError):
                log.exception("Generation failed in chat %s", chat_id)
            finish("".join(parts))
            run.emit("error", error=_error_dict(exc), message=snapshot())
            await ctx.events.emit("generation.failed", chat_id=chat_id, error=exc)
        finally:
            await provider.aclose()
            for target in extras:
                await target.provider.aclose()

    try:
        return ctx.runs.start(chat_id, make_budget(ctx), body)
    except BaseException:
        discard()
        raise
