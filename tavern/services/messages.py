"""Changing messages, with events (ADR 0019 section 2.2-C): the routes of api/chats.py call these, so plugins
(the memory) hear about edits, hides, deletes, swipes and branch changes, whoever made them (the page, the
assistant through `app_call`, another plugin).

Events (actions, see core/events.py), payload keys:
  message.added          message, chat_id
  message.edited         message, chat_id, before (the text before the edit)
  message.excluded       message, chat_id, excluded (True: hidden from the model)
  message.deleted        message (the row before), chat_id, children (ids that moved up)
  message.swipe_selected message, chat_id, before_idx
  chat.branch_selected   chat_id, leaf_id, before_leaf
  chat.created           chat_id
Memory uses them only to WAKE its job and to mark things stale quickly: correctness never depends on them
(a content hash is checked too). `announce` is for sync code (the chat service): it schedules the event on the
running loop and returns at once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tavern.context import AppContext

log = logging.getLogger(__name__)

_pending: set[asyncio.Task[None]] = set()


def announce(ctx: AppContext, event: str, **payload: Any) -> None:
    """Fire an event from sync code: scheduled on the running loop (nothing happens without one)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(ctx.events.emit(event, **payload))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def edit(ctx: AppContext, message_id: str, *, text: str | None = None, original: str | None = None,
               exclude: bool | None = None) -> dict[str, Any]:
    """New text of the shown version (`original`: the text as typed, kept when a rule changed it) and/or hide /
    show the message for the model. Returns the message."""
    message = ctx.chats.get_message(message_id)
    if text is not None:
        ctx.chats.set_content(message_id, text)
        if original is not None:
            ctx.chats.update_swipe_meta(message_id, message["active_swipe"], {"regex_before": original})
    if exclude is not None:
        ctx.chats.set_excluded(message_id, exclude)
    after = ctx.chats.get_message(message_id)
    if text is not None:
        await ctx.events.emit("message.edited", message=after, chat_id=after["chat_id"], before=message["content"])
    if exclude is not None and exclude != message["exclude_from_prompt"]:
        await ctx.events.emit("message.excluded", message=after, chat_id=after["chat_id"], excluded=exclude)
    return after


async def delete(ctx: AppContext, message_id: str) -> dict[str, Any]:
    """Delete one message (what came after it moves up). Returns the chat."""
    message = ctx.chats.get_message(message_id)
    children = [c["id"] for c in ctx.chats.child_messages(message_id)]
    ctx.chats.delete_message(message_id)
    await ctx.events.emit("message.deleted", message=message, chat_id=message["chat_id"], children=children)
    return ctx.chats.get_chat(message["chat_id"])


async def select_swipe(ctx: AppContext, message_id: str, index: int) -> dict[str, Any]:
    before = ctx.chats.get_message(message_id)["active_swipe"]
    message = ctx.chats.set_active_swipe(message_id, index)
    await ctx.events.emit("message.swipe_selected", message=message, chat_id=message["chat_id"], before_idx=before)
    return message


async def select_branch(ctx: AppContext, chat_id: str, leaf_id: str, *, exact: bool = False) -> dict[str, Any]:
    before = ctx.chats.get_chat(chat_id)["leaf_id"]
    ctx.chats.set_leaf(chat_id, leaf_id if exact else ctx.chats.newest_leaf(leaf_id))
    chat = ctx.chats.get_chat(chat_id)
    await ctx.events.emit("chat.branch_selected", chat_id=chat_id, leaf_id=chat["leaf_id"], before_leaf=before)
    return chat
