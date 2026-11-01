"""🎲 Кубики: "/roll 2d6+1" (or "/r") rolls and posts the result in the chat.

A formula is dice and numbers joined by + and -: "d20", "2d6+1", "3к6-2", "1d8+1d6+3".
Russian "к" works as "d". Limits keep a typo from flooding the chat: at most 10 parts,
100 dice in a part, 1000 sides.
"""

from __future__ import annotations

import random
import re
from typing import Any

from tavern.errors import AppError

PART_RE = re.compile(r"([+-]?)(?:(\d*)d(\d+)|(\d+))")
FULL_RE = re.compile(r"^[+-]?(?:\d*d\d+|\d+)(?:[+-](?:\d*d\d+|\d+))*$")
MAX_PARTS = 10
MAX_DICE = 100
MAX_SIDES = 1000


class BadFormula(AppError):
    code = "bad_formula"


def roll(formula: str, rng: random.Random | None = None) -> dict[str, Any]:
    """{"formula", "parts": [{"dice": "2d6", "rolls": [3, 5], "sign": 1} | {"number": 1, "sign": 1}], "total"}."""
    rng = rng or random.SystemRandom()
    text = formula.lower().replace(" ", "").replace("к", "d")
    if not FULL_RE.match(text):
        raise BadFormula(f"Не понимаю «{formula}». Пример: /roll 2d6+1")
    parts: list[dict[str, Any]] = []
    total = 0
    for sign_text, count, sides, number in PART_RE.findall(text):
        sign = -1 if sign_text == "-" else 1
        if number:
            parts.append({"number": int(number), "sign": sign})
            total += sign * int(number)
            continue
        n, s = int(count or 1), int(sides)
        if not 1 <= n <= MAX_DICE or not 2 <= s <= MAX_SIDES:
            raise BadFormula(f"Кубиков от 1 до {MAX_DICE}, граней от 2 до {MAX_SIDES}")
        rolls = [rng.randint(1, s) for _ in range(n)]
        parts.append({"dice": f"{n}d{s}", "rolls": rolls, "sign": sign})
        total += sign * sum(rolls)
    if len(parts) > MAX_PARTS:
        raise BadFormula(f"Не больше {MAX_PARTS} частей в формуле")
    return {"formula": text, "parts": parts, "total": total}


def describe(result: dict[str, Any]) -> str:
    """"🎲 2d6+1: [3, 5] + 1 = 9"."""
    pieces = []
    for i, part in enumerate(result["parts"]):
        sign = "−" if part["sign"] < 0 else "+"
        body = str(part["number"]) if "number" in part else "[" + ", ".join(map(str, part["rolls"])) + "]"
        pieces.append((f"{sign} " if i or part["sign"] < 0 else "") + body)
    return f"🎲 {result['formula']}: {' '.join(pieces)} = {result['total']}"


def setup(api: Any) -> None:
    def on_roll(chat_id: str, args: str) -> str:
        result = roll(args or api.settings["default"] or "1d20")
        text = describe(result)
        api.add_message(chat_id, text, role="system", name="🎲 Кубики", to_model=api.settings["to_model"],
                        extra={"dice": result})
        return text

    api.command("roll", on_roll, "бросить кубики: /roll 2d6+1")
    api.command("r", on_roll, "то же, что /roll")
