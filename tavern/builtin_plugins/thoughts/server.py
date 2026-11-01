"""🌳 Дерево мыслей: before a reply several thinkers look at the scene, then the reply is written.

A reply writer (👥 «Ответ пишет»). Thinkers are every character of the scene except the user's own
(setting `characters`) plus the user's list (`thinkers`: «Мир», «Сюжет» by default). In a round all
of them think at once, each over the standard prompt (cards, lore, history) with its own task; from
the second round on each also sees what the others thought before. Then the chat's model writes the
reply over the standard prompt plus the thoughts as a closing system note.

The thoughts are kept with this version of the reply (`job.meta["thoughts"]`, saved even when the
run is stopped) and are never sent to the model later. Progress goes to the page as trace events of
kind "thought" for the live tree. A thinker that fails is marked and the reply goes on without it;
a refusal of model access (or a run limit) ends the reply, and so does every thinker failing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from tavern.core.budget import BudgetExceeded
from tavern.core.generation import GenerationJob
from tavern.core.llm import LLMClient
from tavern.errors import TavernError
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ChatMessage

GENERATOR_ID = "thoughts"
FATAL = (PluginRefused, BudgetExceeded)  # these end the whole reply, not just one thinker


@dataclass
class Thinker:
    name: str
    task: str
    kind: str  # "character" | "custom"


def thinkers(settings: dict[str, Any], job: GenerationJob) -> list[Thinker]:
    out: list[Thinker] = []
    if settings["characters"] and job.prompt_input is not None:
        for member in job.prompt_input.members:
            if not member.is_me:
                out.append(Thinker(member.name, settings["character_task"], "character"))
    for row in settings["thinkers"]:
        if row.get("on") and str(row.get("name", "")).strip():
            out.append(Thinker(row["name"].strip(), str(row.get("task", "")).strip(), "custom"))
    return out


def instruction(thinker: Thinker, words: int, others: list[dict[str, Any]]) -> str:
    """The closing turn that makes one thinker think instead of writing the reply."""
    if thinker.kind == "character":
        who = (f"Сейчас ты — внутренний голос персонажа {thinker.name}. Не пиши ответ в чат и не говори за "
               f"других. Подумай от лица {thinker.name}, от первого лица.")
    else:
        who = f"Сейчас ты — «{thinker.name}», один из думающих перед следующим ответом. Не пиши сам ответ в чат."
    parts = [who]
    if thinker.task:
        parts.append(thinker.task)
    if others:
        lines = "\n".join(f"[{o['name']}]: {o['text']}" for o in others if o.get("text"))
        parts.append(f"Что подумали другие в прошлом круге:\n{lines}\nУточни свои мысли с учётом этого.")
    parts.append(f"Ответь коротко, примерно {words} слов, без вступлений.")
    return "\n\n".join(parts)


def closing_note(thoughts: list[dict[str, Any]]) -> str:
    lines = "\n".join(f"[{t['name']}]: {t['text']}" for t in thoughts if t.get("text"))
    return ("Перед этим ответом участники обдумали сцену. Используй их мысли как подсказки: что персонажи "
            "чувствуют и хотят, что происходит вокруг, куда идёт история. Не пересказывай и не упоминай "
            f"эти заметки, просто напиши следующий ответ.\n\n{lines}")


class ThoughtsGenerator:
    id = GENERATOR_ID

    def __init__(self, api: Any) -> None:
        self.api = api

    def _client(self, job: GenerationJob) -> tuple[LLMClient, Any]:
        """The thinkers' client and its provider to close (None when it is the chat's own)."""
        settings = self.api.settings
        model = settings["model"].strip() or None
        connection_id = settings["connection"]
        if not connection_id:
            return job.llm.fork(model=model), None
        from tavern.services import spending

        connection = self.api.ctx.connections.get(connection_id)
        provider = self.api.ctx.providers.create(connection)
        meter = spending.meter(self.api.ctx, connection, chat_id=job.chat["id"], message_id=job.message_id,
                               purpose=f"plugin:{self.api.id}")
        # A fork keeps the reply's budget and the plugin's access gate (its count per reply too).
        return job.llm.fork(provider=provider, model=model or connection.get("model", ""), meter=meter), provider

    async def generate(self, job: GenerationJob):  # type: ignore[no-untyped-def]
        settings = self.api.settings
        messages = await job.build_messages()
        cast = thinkers(settings, job)
        rounds: list[list[dict[str, Any]]] = []
        job.meta["thoughts"] = {"rounds": rounds}
        if cast:
            llm, provider = self._client(job)
            try:
                for number in range(int(settings["rounds"])):
                    before = rounds[-1] if rounds else []
                    current = [{"name": t.name, "kind": t.kind, "text": "", "state": "wait"} for t in cast]
                    rounds.append(current)
                    self._trace(job, number, current)
                    results = await asyncio.gather(
                        *(self._think(job, llm, messages, t, settings["words"], number, i, before, current)
                          for i, t in enumerate(cast)),
                        return_exceptions=True)
                    for result in results:
                        if isinstance(result, FATAL) or (isinstance(result, BaseException)
                                                         and not isinstance(result, Exception)):
                            raise result
                    if all(t["state"] == "error" for t in current):
                        raise TavernError("Ни один думающий не ответил: " + current[0].get("error", ""))
            finally:
                if provider is not None:
                    await provider.aclose()
        final = [t for t in rounds[-1] if t["state"] == "done"] if rounds else []
        if final:
            messages = [*messages, ChatMessage("system", closing_note(final))]
        job.run.trace("thought_reply", id=job.message_id, plugin=self.api.id)
        async for event in job.llm.stream(messages, **job.params):
            if event.kind == "text":
                yield event.text
            elif event.kind == "reasoning":
                job.think(event.text)

    async def _think(self, job: GenerationJob, llm: LLMClient, messages: list[ChatMessage], thinker: Thinker,
                     words: int, number: int, index: int, before: list[dict[str, Any]],
                     current: list[dict[str, Any]]) -> None:
        entry = current[index]
        others = [o for o in before if o["name"] != thinker.name and o["state"] == "done"]
        prompt = [*messages, ChatMessage("user", instruction(thinker, int(words), others))]
        try:
            result = await llm.generate(prompt, **job.params)
            text = result.text.strip()
            entry.update(text=text, state="done" if text else "error")
            if not text:
                entry["error"] = "пустой ответ"
        except FATAL as exc:
            entry.update(state="error", error=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - one thinker failing must not cost the reply
            if not isinstance(exc, TavernError):
                self.api.log.exception("Thinker %s failed", thinker.name)
            entry.update(state="error", error=str(exc) or type(exc).__name__)
        finally:
            self._trace(job, number, current, index)

    def _trace(self, job: GenerationJob, number: int, current: list[dict[str, Any]],
               index: int | None = None) -> None:
        """One event per thinker: the page builds the live tree from them (web.js)."""
        for i, entry in enumerate(current):
            if index is None or i == index:
                job.run.trace("thought", id=job.message_id, plugin=self.api.id, round=number, index=i,
                              name=entry["name"], who=entry["kind"], state=entry["state"], text=entry["text"],
                              error=entry.get("error", ""))


def setup(api: Any) -> None:
    api.add_generator(ThoughtsGenerator(api), "🌳 Дерево мыслей")
