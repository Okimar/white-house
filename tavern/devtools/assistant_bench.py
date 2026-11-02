"""Live test stand for the assistant (stage 10а): run a scenario against a real model, keep a report.

    python -m tavern.devtools.assistant_bench lorebook --roles planner=gemini-3.8-flash-high,small=gemini-3.1-flash-lite

A scenario is `tavern/devtools/bench/<name>.json` (or a path):
    {"title": "...", "turns": [{"text": "...", "answers": {}, "go": true}], "look_for": "what a good result has"}
`answers` answers the questions card ({} = every default, i.e. «на твой вкус»; {"q1": "..."} = some);
`go: false` stops at the plan. «Разрешить?» questions are allowed. Each turn waits for the end.

The run happens in a throwaway copy of the data folder (the owner's by default, so their connection
and key are used; nothing in the real folder changes). The report goes to `.bench/<time>-<name>/`:
report.md (questions, plan, progress, critic, report, timings, spending per role, and the full text of
every lorebook and character the run made or changed) and run.json (the same as data).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from tavern.context import AppContext, open_context
from tavern.paths import DEFAULT_DATA_DIR, REPO_ROOT, DataPaths
from tavern.sandbox import make_sandbox, remove_sandbox

SCENARIOS = Path(__file__).with_name("bench")
OUT = REPO_ROOT / ".bench"
MODES = {"always": "🔵 всегда", "search": "🟢 по теме", "off": "⚪ выкл"}


def load_scenario(name: str) -> dict[str, Any]:
    path = Path(name)
    if not path.exists():
        path = SCENARIOS / f"{name}.json"
    scenario = json.loads(path.read_text(encoding="utf-8"))
    scenario.setdefault("name", path.stem)
    return scenario


def configure(ctx: AppContext, args: argparse.Namespace) -> dict[str, Any]:
    from tavern.services import assistant

    settings = assistant.get_settings(ctx)
    if args.source:
        settings["source"] = args.source
    for pair in filter(None, (args.roles or "").split(",")):
        role, _, model = pair.partition("=")
        settings["roles"][role.strip()]["model"] = model.strip()
    if args.model:  # every role without its own model
        for role in assistant.ROLES:
            settings["roles"][role]["model"] = settings["roles"][role]["model"] or args.model
    if args.no_critic:
        settings["critic"] = False
    if args.rounds:
        settings["critic_rounds"] = args.rounds
    if args.steps:
        settings["max_steps"] = args.steps
    return assistant.save_settings(ctx, settings)


async def _wait(ctx: AppContext, conv: str, run: Any, asks: list[str]) -> None:
    """Wait for a run, allowing every «Разрешить?» it asks."""
    from tavern.services import assistant, assistant_engine

    while not run.task.done():
        pending = assistant_engine.pending_ask(conv)
        if pending:
            asks.append(pending["text"])
            assistant.answer(conv, pending["id"], True)
        await asyncio.sleep(0.2)


async def play(ctx: AppContext, scenario: dict[str, Any], chat_id: str | None = None) -> dict[str, Any]:
    from tavern.services import assistant

    conv = assistant.create_chat(ctx)["id"]
    timings: list[dict[str, Any]] = []
    asks: list[str] = []
    journal_from = max([a["id"] for a in ctx.db.fetchall("SELECT id FROM assistant_actions")], default=0)

    async def step(label: str, **kwargs: Any) -> None:
        started = time.monotonic()
        run = assistant.start(ctx, conv, chat_id=chat_id, **kwargs)
        await _wait(ctx, conv, run, asks)
        timings.append({"step": label, "seconds": round(time.monotonic() - started, 1)})
        print(f"  {label}: {timings[-1]['seconds']} s, phase {ctx.assistant_repo.task(conv).get('phase', '')}",
              flush=True)

    for turn in scenario["turns"]:
        await step(f"«{turn['text'][:40]}»", text=turn["text"])
        if ctx.assistant_repo.task(conv).get("phase") == "questions":
            await step("ответы", answers=turn.get("answers", {}))
        if ctx.assistant_repo.task(conv).get("phase") == "plan" and turn.get("go", True):
            await step("давай", go=True)
    return {"conversation": conv, "timings": timings, "asks": asks, "journal_from": journal_from,
            "chat": assistant.get_chat(ctx, conv)}


def products(ctx: AppContext, result: dict[str, Any]) -> dict[str, Any]:
    """Every lorebook and character the run made or changed, in full (the same gathering the critic gets)."""
    from tavern.services.assistant_engine import touched

    actions = [a for a in result["chat"]["actions"] if a["id"] > result["journal_from"] and a["status"] == "done"]
    return {**touched(ctx, actions), "actions": [a["what"] for a in actions]}


def _tree_md(blocks: list[dict[str, Any]]) -> list[str]:
    children: dict[Any, list[dict[str, Any]]] = {}
    for block in blocks:
        children.setdefault(block["parent_id"], []).append(block)
    lines: list[str] = []

    def walk(parent: Any, depth: int) -> None:
        for block in children.get(parent, []):
            extra = []
            if block["keywords"]:
                extra.append("слова: " + ", ".join(block["keywords"]))
            if block.get("chance", 100) < 100:
                extra.append(f"шанс {block['chance']}%")
            if block.get("links"):
                extra.append("🔗 " + ", ".join(block["links"]))
            if block.get("rules"):
                extra.append("правила: " + json.dumps(block["rules"], ensure_ascii=False))
            title = f"{'#' * min(6, depth + 3)} {block['title']} — {MODES.get(block['mode'], block['mode'])}"
            lines.extend([title + (f" ({'; '.join(extra)})" if extra else ""), "", block["content"] or "_(пусто)_", ""])
            walk(block["id"], depth + 1)

    walk(None, 0)
    return lines


def report_md(scenario: dict[str, Any], settings: dict[str, Any], result: dict[str, Any], made: dict[str, Any],
              seconds: float) -> str:
    chat = result["chat"]
    task = chat["task"]
    out = [f"# {scenario.get('title', scenario['name'])}", ""]
    out += [f"Всего: {seconds:.0f} с. Основная модель: `{settings['source'] or 'как в чате'}`; роли: " +
            ", ".join(f"{r} `{s['model'] or 'как основная'}`" for r, s in settings["roles"].items()) +
            f". Критик: {'кругов ' + str(settings['critic_rounds']) if settings['critic'] else 'нет'}.", ""]
    if scenario.get("look_for"):
        out += [f"**Что ищем:** {scenario['look_for']}", ""]
    out += ["## Время", ""] + [f"- {t['step']}: {t['seconds']} с" for t in result["timings"]] + [""]
    if result["asks"]:
        out += ["Разрешено: " + "; ".join(result["asks"]), ""]
    if task.get("spent"):
        out += ["## Расход по ролям", "", "| роль | запросов | токены (вход / выход) | $ |", "|---|---|---|---|"]
        for role, row in task["spent"].items():
            out.append(f"| {role} | {row['requests']} | {row['prompt_tokens']} / {row['completion_tokens']} "
                       f"| {row['cost']:.4f} |")
        out.append("")
    out += ["## Беседа", ""]
    for message in chat["messages"]:
        data, kind = message["data"], message["data"].get("kind", "")
        if message["role"] == "tool":
            errors = [f"{r.get('tool')}: {r['error']}" for r in data.get("results", []) if "error" in r]
            if errors:
                out.append("> ⚠ ошибки инструментов: " + "; ".join(errors))
        elif message["role"] == "user":
            out += ["", f"**Вы:** {message['text']}", ""]
        elif kind == "questions":
            out.append(f"**Вопросы:** {message['text']}")
            for q in data["questions"]:
                chosen = task.get("answers", {}).get(q["id"], "")
                out.append(f"- {q['text']} — " + " / ".join(f"**{o}**" if o == chosen else o for o in q["options"]))
            out.append("")
        elif kind == "plan":
            out += [f"**План:** {message['text']}", f"Цель: {data.get('goal', '')}", "", "Решения:"]
            out += [f"- {d}" for d in data.get("decisions", [])] + ["", "Пункты:"]
            out += [f"{i['id']}. {i['title']} — {i.get('detail', '')}" for i in data["items"]] + [""]
        elif kind == "critic":
            out.append(f"🔍 **{message['text']}**")
            out += [f"  - {i.get('where', '')}: {i['problem']} → {i.get('fix', '')}" for i in data.get("issues", [])]
        elif kind == "progress":
            out.append(f"▸ {message['text']}")
        elif kind == "step":
            out.append(f"  · {message['text']}")
        elif message["text"]:
            out += ["", f"**Помощник{' (итог)' if kind == 'report' else ''}:** {message['text']}", ""]
    if task.get("items"):
        out += ["", "## Пункты плана в конце", ""]
        out += [f"- {i['id']} [{i['status']}] {i['title']}: {i.get('summary', '')}" for i in task["items"]]
    out += ["", "## Журнал изменений", ""] + [f"- {w}" for w in made["actions"]]
    for book in made["lorebooks"]:
        out += ["", f"## Лорбук «{book['name']}» — записей: {len(book['entries'])}", "", book["description"] or "", ""]
        out += _tree_md(book["entries"])
    for char in made["characters"]:
        data = char["data"].get("data", char["data"])
        out += ["", f"## Персонаж «{char['name']}»", ""]
        for field in ("description", "personality", "scenario", "first_mes", "mes_example"):
            if data.get(field):
                out += [f"**{field}:**", "", data[field], ""]
        out += _tree_md(char["bubbles"])
    return "\n".join(out) + "\n"


async def run_scenario(args: argparse.Namespace) -> Path:
    scenario = load_scenario(args.scenario)
    box = make_sandbox(DataPaths(Path(args.data_dir) if args.data_dir else DEFAULT_DATA_DIR))
    ctx = open_context(box, sandbox=True, load_plugins=False)
    try:
        settings = configure(ctx, args)
        print(f"Scenario {scenario['name']} in {box.root}", flush=True)
        started = time.monotonic()
        result = await play(ctx, scenario)
        seconds = time.monotonic() - started
        made = products(ctx, result)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{scenario['name']}" + (f"-{args.label}" if args.label else "")
        folder = Path(args.out) / name if args.out else OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.md").write_text(report_md(scenario, settings, result, made, seconds), encoding="utf-8")
        (folder / "run.json").write_text(json.dumps({"scenario": scenario, "settings": settings, "seconds": seconds,
                                                     "result": result, "made": made}, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        print(f"Done in {seconds:.0f} s: {folder / 'report.md'}", flush=True)
        return folder
    finally:
        ctx.close()
        if not args.keep:
            remove_sandbox(box)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run an assistant scenario against a live model and keep a report.")
    p.add_argument("scenario", help="name in tavern/devtools/bench/ or a path to a scenario file")
    p.add_argument("--data-dir", help="data folder to copy (default: ./data)")
    p.add_argument("--out", help="where report folders go (default: .bench/)")
    p.add_argument("--source", help="the assistant's model source: connection:<id> or profile:<id>")
    p.add_argument("--model", help="model name for every role that has none")
    p.add_argument("--roles", help="role=model,... (planner, writer, critic, small)")
    p.add_argument("--no-critic", action="store_true")
    p.add_argument("--rounds", type=int, help="critic rounds")
    p.add_argument("--steps", type=int, help="steps per request and per plan item")
    p.add_argument("--label", help="added to the report folder name")
    p.add_argument("--keep", action="store_true", help="keep the sandbox copy")
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    asyncio.run(run_scenario(args))


if __name__ == "__main__":
    main()
