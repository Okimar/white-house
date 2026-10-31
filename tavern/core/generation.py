"""The generation pipeline and how plugins replace it.

A `Generator` turns a `GenerationJob` into a stream of reply text. The default generator builds a
prompt and streams one model call. A plugin registers its own generator (for example one that runs
several agents and then writes the final answer) and selects it per chat (`chat.meta["generator"]`)
or globally (setting `generator`). A custom generator may call `job.build_messages()` to reuse the
standard prompt, `job.llm` (shared budget, forkable to other providers/models) and `job.run.trace()`
to report progress to the UI. The model's thinking (stream events of kind "reasoning") goes to
`job.think(text)`: it is shown folded above the reply and kept with it, never mixed into the text.

Events fired around a run (see core/events.py):
  chat.before_generate  action  job
  prompt.built          filter  messages -> messages   (job=...)
  message.received      action  message, chat
  generation.failed     action  chat_id, error
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from tavern.core.assembly import Prompt, PromptInput, assemble
from tavern.core.llm import LLMClient
from tavern.core.prompt import build_prompt, estimate_tokens
from tavern.core.runs import Run
from tavern.providers.base import ChatMessage

if TYPE_CHECKING:
    from tavern.context import AppContext

DEFAULT_GENERATOR = "default"


@dataclass
class GenerationJob:
    ctx: AppContext
    chat: dict[str, Any]
    card: dict[str, Any] | None
    history: list[dict[str, Any]]  # active branch, oldest first, excluding the message being written
    user_name: str
    llm: LLMClient
    run: Run
    max_context_tokens: int = 0
    params: dict[str, Any] = field(default_factory=dict)  # per-call model options (temperature...)
    instruction: str = ""  # extra final user turn, e.g. for impersonation
    # Stage 4а: when set, the prompt comes from the preset and chat members (core/assembly.py).
    prompt_input: PromptInput | None = None
    # Filled by build_messages: how the prompt was made and what was finally sent.
    prompt: Prompt | None = None
    sent: list[ChatMessage] | None = None
    reasoning: list[str] = field(default_factory=list)
    on_reasoning: Any = None  # callable(text) set by the chat service to stream thinking to the UI

    def think(self, text: str) -> None:
        self.reasoning.append(text)
        if self.on_reasoning is not None:
            self.on_reasoning(text)

    async def build_messages(self) -> list[ChatMessage]:
        """Standard prompt for this job, after `prompt.built` filters."""
        if self.prompt_input is not None:
            self.prompt = assemble(self.prompt_input)
            messages = list(self.prompt.messages)
        else:
            messages = build_prompt(
                self.card,
                self.history,
                user_name=self.user_name,
                max_context_tokens=self.max_context_tokens,
                instruction=self.instruction,
            )
        messages = await self.ctx.events.filter("prompt.built", messages, job=self)
        self.sent = messages
        self.run.trace(
            "prompt",
            messages=len(messages),
            tokens=sum(estimate_tokens(m.content) for m in messages),
        )
        return messages


class Generator(Protocol):
    id: str

    def generate(self, job: GenerationJob) -> AsyncIterator[str]:
        """Yield the reply as text deltas."""
        ...


class DefaultGenerator:
    id = DEFAULT_GENERATOR

    async def generate(self, job: GenerationJob) -> AsyncIterator[str]:
        messages = await job.build_messages()
        async for event in job.llm.stream(messages, **job.params):
            if event.kind == "text":
                yield event.text
            elif event.kind == "reasoning":
                job.think(event.text)


class GeneratorRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Generator] = {DEFAULT_GENERATOR: DefaultGenerator()}

    def register(self, generator: Generator) -> None:
        self._items[generator.id] = generator

    def unregister(self, generator_id: str) -> None:
        if generator_id != DEFAULT_GENERATOR:
            self._items.pop(generator_id, None)

    @property
    def ids(self) -> list[str]:
        return sorted(self._items)

    def get(self, generator_id: str | None) -> Generator:
        """Unknown ids fall back to the default so a removed plugin never blocks chatting."""
        return self._items.get(generator_id or DEFAULT_GENERATOR) or self._items[DEFAULT_GENERATOR]
