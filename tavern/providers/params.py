"""Connection settings: every knob the settings window can show, and which provider kinds take it.

A connection keeps its settings in `params` under the names below. A setting that is absent is not
sent, so the backend uses its own default. Kinds map the names to their wire format (OpenAI-style
names go out as they are; Gemini puts them into `generationConfig`). Settings with `send=False`
steer Tavern itself (context size, retries) and never reach the backend.
"""

from __future__ import annotations

from typing import Any

OPENAI = "openai"
GEMINI = "gemini"
ANTHROPIC = "anthropic"
ALL = (OPENAI, GEMINI, ANTHROPIC)
OG = (OPENAI, GEMINI)

PARAMS: list[dict[str, Any]] = [
    # Sampling
    {"id": "temperature", "group": "sampling", "label": "Температура", "type": "float", "min": 0, "max": 2,
     "step": 0.01, "default": 1.0, "kinds": ALL, "hint": "Выше — смелее и разнообразнее, ниже — предсказуемее"},
    {"id": "top_p", "group": "sampling", "label": "Top P", "type": "float", "min": 0, "max": 1, "step": 0.01,
     "default": 1.0, "kinds": ALL, "hint": "Брать слова из самых вероятных, пока их доля не наберёт P"},
    {"id": "top_k", "group": "sampling", "label": "Top K", "type": "int", "min": 0, "max": 500, "step": 1,
     "default": 0, "kinds": ALL, "hint": "Выбирать только из K самых вероятных слов (0 — без ограничения)"},
    {"id": "min_p", "group": "sampling", "label": "Min P", "type": "float", "min": 0, "max": 1, "step": 0.01,
     "default": 0.0, "kinds": (OPENAI,), "hint": "Отбрасывать слова, которые намного менее вероятны лучшего"},
    {"id": "top_a", "group": "sampling", "label": "Top A", "type": "float", "min": 0, "max": 1, "step": 0.01,
     "default": 0.0, "kinds": (OPENAI,), "hint": "Отсечка по квадрату вероятности лучшего слова"},
    {"id": "typical_p", "group": "sampling", "label": "Typical P", "type": "float", "min": 0, "max": 1,
     "step": 0.01, "default": 1.0, "kinds": (OPENAI,), "hint": "Оставлять «типичные» по неожиданности слова"},
    {"id": "tfs", "group": "sampling", "label": "Tail free", "type": "float", "min": 0, "max": 1, "step": 0.01,
     "default": 1.0, "kinds": (OPENAI,), "hint": "Срезать хвост маловероятных слов"},
    # Repetition
    {"id": "repetition_penalty", "group": "repeat", "label": "Штраф за повтор", "type": "float", "min": 0,
     "max": 3, "step": 0.01, "default": 1.0, "kinds": (OPENAI,), "hint": "Больше 1 — реже повторяет слова"},
    {"id": "frequency_penalty", "group": "repeat", "label": "Штраф за частоту", "type": "float", "min": -2,
     "max": 2, "step": 0.01, "default": 0.0, "kinds": OG, "hint": "Чем чаще слово уже было, тем реже оно снова"},
    {"id": "presence_penalty", "group": "repeat", "label": "Штраф за присутствие", "type": "float",
     "min": -2, "max": 2, "step": 0.01, "default": 0.0, "kinds": OG,
     "hint": "Слово, которое уже было, — реже; толкает к новым темам"},
    # Length and shape
    {"id": "max_tokens", "group": "length", "label": "Длина ответа", "type": "int", "min": 1,
     "max": 1_000_000, "step": 1, "default": 1024, "kinds": ALL, "hint": "Предел длины ответа в токенах"},
    {"id": "stop", "group": "length", "label": "Стоп-строки", "type": "list", "default": [], "kinds": ALL,
     "hint": "Модель останавливается, когда напишет одну из них"},
    {"id": "seed", "group": "length", "label": "Зерно", "type": "int", "min": -1, "max": 2**31 - 1, "step": 1,
     "default": -1, "kinds": OG, "hint": "Одно и то же зерно — одинаковый ответ на одинаковый запрос"},
    {"id": "logit_bias", "group": "length", "label": "Сдвиг слов", "type": "json", "default": {},
     "kinds": (OPENAI,), "hint": "Номер токена → сдвиг от -100 до 100"},
    # Thinking
    {"id": "reasoning_effort", "group": "thinking", "label": "Глубина размышлений", "type": "choice",
     "choices": ["none", "minimal", "low", "medium", "high"], "default": "medium", "kinds": OG,
     "hint": "Сколько модель думает перед ответом (для думающих моделей)"},
    {"id": "thinking_budget", "group": "thinking", "label": "Бюджет размышлений", "type": "int", "min": -1,
     "max": 32768, "step": 1, "default": -1, "kinds": (GEMINI, ANTHROPIC),
     "hint": "Токенов на размышления; -1 — сама решает, 0 — не думать"},
    {"id": "include_thoughts", "group": "thinking", "label": "Присылать размышления", "type": "bool",
     "default": True, "kinds": (GEMINI,), "hint": "Модель присылает ход мыслей отдельно от ответа"},
    # Tools
    {"id": "web_search", "group": "tools", "label": "Поиск в интернете", "type": "bool", "default": True,
     "kinds": ALL, "hint": "Модель может искать в интернете (где сервис это умеет; бывает платно)"},
    # Safety
    {"id": "safety_off", "group": "safety", "label": "Выключить фильтры", "type": "bool", "default": True,
     "kinds": (GEMINI,), "hint": "Просить сервис не блокировать ответы фильтрами безопасности"},
    # Raw
    {"id": "extra_body", "group": "raw", "label": "Свои поля запроса", "type": "json", "default": {},
     "kinds": ALL, "hint": "Добавляются в запрос как есть, поверх всего остального"},
    # How requests go (OpenAI-style servers only)
    {"id": "api_mode", "group": "tavern", "label": "Режим запросов", "type": "choice", "choices": ["chat", "text"],
     "default": "chat", "kinds": (OPENAI,), "send": False,
     "hint": "chat — сообщения по ролям; text — один текст по шаблону разметки (для локальных моделей)"},
    {"id": "template", "group": "tavern", "label": "Шаблон разметки", "type": "template", "default": "auto",
     "kinds": (OPENAI,), "send": False, "hint": "Для режима text; «auto» — по названию модели"},
    # If it fails (shown in their own section, not in the console)
    {"id": "fallbacks", "group": "fallback", "label": "Запасные пути", "type": "fallbacks", "default": [],
     "kinds": ALL, "send": False, "hint": "Что пробовать, если подключение не ответило"},
    {"id": "refusal_phrases", "group": "fallback", "label": "Фразы отказа", "type": "list", "default": [],
     "kinds": ALL, "send": False,
     "hint": "Короткий ответ с такой фразой считается отказом (пусто — встроенный список)"},
    # Tavern itself
    {"id": "max_context_tokens", "group": "tavern", "label": "Размер контекста", "type": "int", "min": 0,
     "max": 10_000_000, "step": 1, "default": 0, "kinds": ALL, "send": False,
     "hint": "Сколько токенов помещается в модель; 0 — без ограничения"},
    {"id": "rotate_keys", "group": "tavern", "label": "Переходить на следующий ключ", "type": "bool",
     "default": True, "kinds": ALL, "send": False,
     "hint": "Если у ключа кончился лимит или ключ не принят — взять следующий"},
    {"id": "retries", "group": "tavern", "label": "Повторы при сбое", "type": "int", "min": 0, "max": 10,
     "step": 1, "default": 0, "kinds": ALL, "send": False, "hint": "Сколько раз повторить, если сервис не ответил"},
    {"id": "retry_delay", "group": "tavern", "label": "Пауза перед повтором, с", "type": "float", "min": 0,
     "max": 120, "step": 0.5, "default": 2.0, "kinds": ALL, "send": False, "hint": ""},
]

GROUPS = [
    ("sampling", "Выбор слов"), ("repeat", "Повторы"), ("length", "Длина и форма"),
    ("thinking", "Размышления"), ("tools", "Инструменты"), ("safety", "Фильтры"), ("raw", "Как есть"), ("tavern", "Таверна"),
]

BY_ID = {p["id"]: p for p in PARAMS}


def catalog() -> dict[str, Any]:
    return {"params": [{**p, "kinds": list(p["kinds"])} for p in PARAMS], "groups": GROUPS}


def request_params(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """The settings of a connection that go to a backend of `kind`.

    Unknown names (from plugins or older versions) pass through, and a kind this table does not
    know (a plugin's) gets every sendable setting: it may understand them.
    """
    out: dict[str, Any] = {}
    for name, value in params.items():
        spec = BY_ID.get(name)
        if spec is None or (spec.get("send", True) and (kind in spec["kinds"] or kind not in ALL)):
            out[name] = value
    return out


def tavern_setting(params: dict[str, Any], name: str) -> Any:
    """A Tavern-side setting with its default."""
    return params.get(name, BY_ID[name]["default"])


def validate(params: dict[str, Any]) -> dict[str, Any]:
    """Check the known settings; unknown names pass (plugins). Raises ValueError with a message
    for the user."""
    out: dict[str, Any] = {}
    for name, value in params.items():
        spec = BY_ID.get(name)
        if spec is None:
            out[name] = value
            continue
        label, kind = spec["label"], spec["type"]
        if kind in ("float", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"«{label}» должно быть числом")
            if kind == "int" and value != int(value):
                raise ValueError(f"«{label}» должно быть целым")
            if not spec["min"] <= value <= spec["max"]:
                raise ValueError(f"«{label}»: от {spec['min']} до {spec['max']}")
            value = int(value) if kind == "int" else float(value)
        elif kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"«{label}»: да или нет")
        elif kind == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"«{label}»: список строк")
        elif kind == "json":
            if not isinstance(value, dict):
                raise ValueError(f"«{label}»: объект JSON")
        elif kind == "fallbacks":
            from tavern.providers.fallback import check_steps  # fallback imports this module's users

            value = check_steps(value)
        elif kind == "template":
            if not isinstance(value, str) or not value:
                raise ValueError(f"«{label}»: выберите шаблон")
        elif kind == "choice" and value not in spec["choices"]:
            raise ValueError(f"«{label}»: одно из {', '.join(spec['choices'])}")
        out[name] = value
    return out
