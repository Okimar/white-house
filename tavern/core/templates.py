"""Instruct templates: how a conversation becomes one text for models without a chat API.

Local servers (KoboldCpp, llama.cpp, text-generation-webui...) also offer plain text completion:
the whole conversation goes as one string, wrapped in the markers the model was trained on.
A template says which markers wrap each role, which text starts the reply, and where to stop.

Shape (our own, `validate()` checks it):
    name, models (regex of model names it fits, for "auto"),
    bos (text at the very start), story_string (wraps the leading system text, "{{system}}"),
    chat_start (text between that and the conversation),
    system_prefix/suffix, user_prefix/suffix, assistant_prefix/suffix,
    system_as_user (the model has no system role: system text goes as a user turn),
    stop (strings that end the reply; the next user marker is added automatically),
    first_user_prefix / first_assistant_prefix (the chat's first message; empty = the normal one),
    last_user_prefix / last_assistant_prefix (the last user message, and the line that opens the reply),
    sequences_as_stops (the markers also end the reply, line by line; on)

Wave 6 (SillyTavern parity): `{{user}}`, `{{char}}` and `{{name}}` (that message's speaker) work inside every marker;
the user's and the characters' "\nName:" end the reply when the names are known; «Написать за меня» opens the user's
turn instead of sending an instruction. No built-in has a BOS text: the backend adds it, as with SillyTavern.

SillyTavern instruct and context files convert both ways (`from_st` / `to_st`). Its story string
is a template over card fields; here the preset already puts those into the system text, so only
`{{system}}` (and `{{#if system}}` blocks) is kept on import (on purpose: our prompt manager builds the card parts).
"""

from __future__ import annotations

import re
from typing import Any

from tavern.providers.base import ChatMessage, spoken

TEXT_FIELDS = ("bos", "story_string", "chat_start", "system_prefix", "system_suffix", "user_prefix",
               "user_suffix", "assistant_prefix", "assistant_suffix", "first_user_prefix",
               "first_assistant_prefix", "last_user_prefix", "last_assistant_prefix")
MAX_TEXT = 2000


def blank() -> dict[str, Any]:
    """An empty template: every field with its default."""
    return _t("", "")


def _t(name: str, models: str, **fields: Any) -> dict[str, Any]:
    base = {"name": name, "models": models, "bos": "", "story_string": "{{system}}", "chat_start": "",
            "system_prefix": "", "system_suffix": "", "user_prefix": "", "user_suffix": "",
            "assistant_prefix": "", "assistant_suffix": "", "system_as_user": False, "stop": [],
            "first_user_prefix": "", "first_assistant_prefix": "", "last_user_prefix": "",
            "last_assistant_prefix": "", "sequences_as_stops": True}
    return {**base, **fields}


BUILTIN: dict[str, dict[str, Any]] = {
    "chatml": _t("ChatML (Qwen, Hermes, Yi…)", r"qwen|hermes|chatml|yi-|dolphin|magnum",
                 system_prefix="<|im_start|>system\n", system_suffix="<|im_end|>\n",
                 user_prefix="<|im_start|>user\n", user_suffix="<|im_end|>\n",
                 assistant_prefix="<|im_start|>assistant\n", assistant_suffix="<|im_end|>\n",
                 stop=["<|im_end|>"]),
    "llama3": _t("Llama 3", r"llama-?3",
                 system_prefix="<|start_header_id|>system<|end_header_id|>\n\n", system_suffix="<|eot_id|>",
                 user_prefix="<|start_header_id|>user<|end_header_id|>\n\n", user_suffix="<|eot_id|>",
                 assistant_prefix="<|start_header_id|>assistant<|end_header_id|>\n\n", assistant_suffix="<|eot_id|>",
                 stop=["<|eot_id|>"]),
    "llama2": _t("Llama 2 Chat", r"llama-?2.*chat",
                 system_prefix="[INST] <<SYS>>\n", system_suffix="\n<</SYS>>\n\n",
                 user_prefix="[INST] ", user_suffix=" [/INST]", assistant_prefix=" ", assistant_suffix=" </s><s>",
                 stop=["</s>"]),
    "mistral": _t("Mistral (v7 / Tekken)", r"mistral|mixtral|nemo|ministral|magistral|pixtral",
                  system_prefix="[SYSTEM_PROMPT]", system_suffix="[/SYSTEM_PROMPT]",
                  user_prefix="[INST]", user_suffix="[/INST]", assistant_prefix="", assistant_suffix="</s>",
                  stop=["</s>"]),
    "gemma": _t("Gemma 2 / 3", r"gemma",
                user_prefix="<start_of_turn>user\n", user_suffix="<end_of_turn>\n",
                assistant_prefix="<start_of_turn>model\n", assistant_suffix="<end_of_turn>\n",
                system_as_user=True, stop=["<end_of_turn>"]),
    "phi3": _t("Phi-3 / Phi-4", r"phi-?[34]",
               system_prefix="<|system|>\n", system_suffix="<|end|>\n", user_prefix="<|user|>\n",
               user_suffix="<|end|>\n", assistant_prefix="<|assistant|>\n", assistant_suffix="<|end|>\n",
               stop=["<|end|>"]),
    "command_r": _t("Command R", r"command-?r|c4ai",
                    system_prefix="<|START_OF_TURN_TOKEN|><|SYSTEM_TOKEN|>", system_suffix="<|END_OF_TURN_TOKEN|>",
                    user_prefix="<|START_OF_TURN_TOKEN|><|USER_TOKEN|>", user_suffix="<|END_OF_TURN_TOKEN|>",
                    assistant_prefix="<|START_OF_TURN_TOKEN|><|CHATBOT_TOKEN|>",
                    assistant_suffix="<|END_OF_TURN_TOKEN|>", stop=["<|END_OF_TURN_TOKEN|>"]),
    "glm4": _t("GLM-4", r"glm-?4",
               system_prefix="<|system|>\n", user_prefix="<|user|>\n",
               assistant_prefix="<|assistant|>\n", stop=["<|user|>", "<|endoftext|>"]),
    "deepseek": _t("DeepSeek V2.5 / V3", r"deepseek",
                   user_prefix="<｜User｜>", assistant_prefix="<｜Assistant｜>",
                   assistant_suffix="<｜end▁of▁sentence｜>", stop=["<｜end▁of▁sentence｜>"]),
    "metharme": _t("Metharme / Pygmalion", r"pygmalion|metharme",
                   system_prefix="<|system|>", user_prefix="<|user|>", assistant_prefix="<|model|>",
                   stop=["<|user|>"]),
    "alpaca": _t("Alpaca", r"alpaca",
                 system_suffix="\n\n", user_prefix="### Instruction:\n", user_suffix="\n\n",
                 assistant_prefix="### Response:\n", assistant_suffix="\n\n", stop=["### Instruction:"]),
    "vicuna": _t("Vicuna", r"vicuna|wizardlm",
                 system_suffix="\n\n", user_prefix="USER: ", user_suffix="\n", assistant_prefix="ASSISTANT: ",
                 assistant_suffix="</s>\n", stop=["USER:", "</s>"]),
    "plain": _t("Без разметки (имя: текст)", "",
                system_suffix="\n\n", user_prefix="User: ", user_suffix="\n", assistant_prefix="Assistant: ",
                assistant_suffix="\n", stop=["\nUser:"]),
}
FALLBACK = "chatml"


def detect(model: str, templates: dict[str, dict[str, Any]]) -> str:
    """Id of the first template whose `models` pattern matches the model name; FALLBACK otherwise."""
    name = model.lower().rsplit("/", 1)[-1]
    for template_id, template in templates.items():
        pattern = template.get("models") or ""
        if not pattern:
            continue
        try:
            if re.search(pattern, name, re.IGNORECASE):
                return template_id
        except re.error:
            continue
    return FALLBACK


def render(template: dict[str, Any], messages: list[ChatMessage], *, names: dict[str, str] | None = None,
           turn: str = "assistant") -> tuple[str, list[str]]:
    """The prompt text and stop strings. The text ends opened for the reply: with the assistant
    marker, or inside the last assistant turn when the conversation ends with one (continue).
    `names` ({"user", "char"}) fill `{{user}}`, `{{char}}` and `{{name}}` in the markers and add "\\nName:" stops;
    `turn` = "user" (text-mode «Написать за меня») opens the user's turn instead."""
    t = {**_t("", ""), **template}
    names = names or {}
    user, char = names.get("user", ""), names.get("char", "")

    def mark(key: str, speaker: str = "") -> str:
        text = t[key]
        if "{{" not in text:
            return text
        return (text.replace("{{name}}", speaker).replace("{{user}}", user or "User")
                .replace("{{char}}", char or "Assistant"))

    def speaker_of(role: str) -> str:
        return {"user": user, "assistant": char}.get(role, "System")

    leading: list[str] = []
    rest = list(messages)
    while rest and rest[0].role == "system":
        leading.append(spoken(rest.pop(0)))  # a named example line keeps its speaker in front
    parts = [t["bos"]]
    if leading:
        story = _story(t["story_string"], "\n\n".join(leading))
        if t["system_as_user"]:
            rest.insert(0, ChatMessage("user", story))
        else:
            parts.append(mark("system_prefix", "System") + story + mark("system_suffix", "System"))
    parts.append(t["chat_start"])
    roles = [("user" if m.role == "system" and (t["system_as_user"] or not t["system_prefix"]) else m.role)
             for m in rest]
    last_user = max((i for i, r in enumerate(roles) if r == "user"), default=-1)
    seen_first = False
    for i, message in enumerate(rest):
        role = roles[i]
        prefix_key = f"{role}_prefix"
        if role in ("user", "assistant") and not seen_first:
            seen_first = True
            if t[f"first_{role}_prefix"]:
                prefix_key = f"first_{role}_prefix"  # the chat's first message
        if role == "user" and i == last_user and t["last_user_prefix"] and turn != "user":
            prefix_key = "last_user_prefix"
        if i == len(rest) - 1 and role == "assistant" and turn == "assistant":
            opener = "last_assistant_prefix" if t["last_assistant_prefix"] else prefix_key
            parts.append(mark(opener, char) + message.content)  # continue: leave the turn open
            return "".join(parts), _stops(t, names, turn)
        parts.append(mark(prefix_key, speaker_of(role)) + spoken(message) + mark(f"{role}_suffix", speaker_of(role)))
    if turn == "user":
        opener = "last_user_prefix" if t["last_user_prefix"] else "user_prefix"
        parts.append(mark(opener, user))
    else:
        opener = "last_assistant_prefix" if t["last_assistant_prefix"] else "assistant_prefix"
        parts.append(mark(opener, char))
    return "".join(parts), _stops(t, names, turn)


def _story(story_string: str, system: str) -> str:
    text = story_string or "{{system}}"
    text = re.sub(r"\{\{#if system\}\}(.*?)\{\{/if\}\}", lambda m: m.group(1) if system else "", text, flags=re.S)
    text = re.sub(r"\{\{#if [^}]*\}\}.*?\{\{/if\}\}", "", text, flags=re.S)  # card fields: the preset has them
    if "{{system}}" not in text:
        return system
    text = text.replace("{{system}}", system)
    return re.sub(r"\{\{[^{}]*\}\}", "", text).strip("\n")


def _stops(t: dict[str, Any], names: dict[str, str] | None = None, turn: str = "assistant") -> list[str]:
    """The template's stops, the next marker of the other side, the markers themselves line by line (SillyTavern's
    «sequences as stop strings») and "\\nName:" of whoever must not be written for."""
    stops = [s for s in t["stop"] if s]

    def add(value: str) -> None:
        value = value.strip()
        if value and "{{" not in value and value not in stops:
            stops.append(value)

    add(t["user_prefix"] if turn == "assistant" else t["assistant_prefix"])
    if t.get("sequences_as_stops", True):
        keys = (("user_prefix", "first_user_prefix", "last_user_prefix", "system_prefix") if turn == "assistant"
                else ("assistant_prefix", "first_assistant_prefix", "last_assistant_prefix", "system_prefix"))
        for key in keys:
            for line in (t.get(key) or "").split("\n"):
                add(line)
    names = names or {}
    other = names.get("user") if turn == "assistant" else names.get("char")
    if other:
        stop = f"\n{other}:"
        if stop not in stops:
            stops.append(stop)
    return stops


def validate(raw: Any) -> dict[str, Any]:
    """Our template shape from untrusted JSON. Raises ValueError with a message for the user."""
    if not isinstance(raw, dict):
        raise ValueError("Шаблон должен быть объектом JSON")
    out = _t(str(raw.get("name") or "").strip()[:100], str(raw.get("models") or "")[:300])
    if not out["name"]:
        raise ValueError("У шаблона должно быть название")
    try:
        re.compile(out["models"])
    except re.error as exc:
        raise ValueError(f"Шаблон названий моделей не разбирается: {exc}") from None
    for name in TEXT_FIELDS:
        value = raw.get(name, out[name])
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise ValueError(f"Поле «{name}»: строка до {MAX_TEXT} знаков")
        out[name] = value
    out["system_as_user"] = bool(raw.get("system_as_user", False))
    out["sequences_as_stops"] = bool(raw.get("sequences_as_stops", True))
    stop = raw.get("stop", [])
    if not isinstance(stop, list) or len(stop) > 20 or not all(isinstance(s, str) and len(s) <= 200 for s in stop):
        raise ValueError("«stop»: список до 20 строк")
    out["stop"] = [s for s in stop if s]
    return out


# SillyTavern files (instruct and context), from their observed JSON shape.

def is_st(raw: Any) -> bool:
    return isinstance(raw, dict) and any(k in raw for k in ("input_sequence", "output_sequence", "story_string"))


def from_st(raw: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    """An ST instruct file or an ST context file as our template. Pass an existing template as
    `base` to merge the other half into it."""
    out = dict(base) if base else _t(str(raw.get("name") or "Из SillyTavern"), "")
    if "input_sequence" in raw or "output_sequence" in raw:
        # SillyTavern's `wrap`: one line break between the marker and the text; an empty suffix becomes a line
        # break, a non-empty one is left as it is (never a line break in front of it).
        wrap = bool(raw.get("wrap", False))

        def prefix(key: str) -> str:
            value = str(raw.get(key) or "")
            return value + "\n" if value and wrap and not value.endswith("\n") else value

        def suffix(key: str) -> str:
            value = str(raw.get(key) or "")
            return "\n" if wrap and not value else value

        out.update(
            name=str(raw.get("name") or out["name"]),
            models=str(raw.get("activation_regex") or ""),
            user_prefix=prefix("input_sequence"), user_suffix=suffix("input_suffix"),
            assistant_prefix=prefix("output_sequence"), assistant_suffix=suffix("output_suffix"),
            system_prefix=prefix("system_sequence"), system_suffix=suffix("system_suffix"),
            first_user_prefix=prefix("first_input_sequence"), first_assistant_prefix=prefix("first_output_sequence"),
            last_user_prefix=prefix("last_input_sequence"), last_assistant_prefix=prefix("last_output_sequence"),
            system_as_user=bool(raw.get("system_same_as_user", False)),
            sequences_as_stops=raw.get("sequences_as_stop_strings", True) is not False,
            stop=[s for s in [str(raw.get("stop_sequence") or "")] if s],
        )
        if out["system_as_user"]:
            out["system_prefix"], out["system_suffix"] = "", ""
    if "story_string" in raw:
        out["story_string"] = str(raw.get("story_string") or "{{system}}")
        start = str(raw.get("chat_start") or "")
        out["chat_start"] = start + "\n" if start else ""
        if not base and raw.get("name"):
            out["name"] = str(raw["name"])
    return validate(out)


def to_st(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Our template as an ST instruct file and an ST context file."""
    t = {**_t("", ""), **template}
    instruct = {
        "name": t["name"], "input_sequence": t["user_prefix"], "input_suffix": t["user_suffix"],
        "output_sequence": t["assistant_prefix"], "output_suffix": t["assistant_suffix"],
        "system_sequence": t["system_prefix"], "system_suffix": t["system_suffix"],
        "first_input_sequence": t["first_user_prefix"], "first_output_sequence": t["first_assistant_prefix"],
        "last_input_sequence": t["last_user_prefix"], "last_output_sequence": t["last_assistant_prefix"],
        "stop_sequence": (t["stop"] or [""])[0], "sequences_as_stop_strings": bool(t["sequences_as_stops"]),
        "wrap": False, "macro": True, "names_behavior": "none", "activation_regex": t["models"],
        "system_same_as_user": t["system_as_user"], "user_alignment_message": "",
    }
    story = t["story_string"]
    if "{{#if system}}" not in story:
        story = story.replace("{{system}}", "{{#if system}}{{system}}\n{{/if}}", 1)
    context = {"name": t["name"], "story_string": story, "chat_start": t["chat_start"].rstrip("\n"),
               "example_separator": "***"}
    return {"instruct": instruct, "context": context}
