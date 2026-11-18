"""Run the tests a change can break, in one go: the everyday tier plus every test file the change touches.

    python -m tavern.devtools.affected            # changed files from git (against HEAD, plus new files)
    python -m tavern.devtools.affected --list     # only print which test files and why
    python -m tavern.devtools.affected tavern/core/retrieval.py web/components/Lorebooks.js

Why: the full run takes about 5 minutes. Finding the old tests a change breaks by running everything again and
again cost the owner 20 minutes on a half-minute task (2026-11-18). This finds them first:

- test files that import a changed module (`import tavern.x.y`, `from tavern.x import y`, `from tavern.x.y import …`);
- test files named after it (`retrieval.py` -> `test_retrieval*.py`) or naming it in their text (`lorebooks`);
- test files that contain a value the change removed: a number or a quoted string on a deleted line (a threshold
  `0.30` -> `0.40` finds the test that still expects `0.30`);
- changed test files themselves; a changed `web/` file brings the tests that name it.

Then it runs `pytest -m "fast or affected"` once (the files above are marked `affected` through the environment
variable TAVERN_AFFECTED, see tests/conftest.py). The full `python -m pytest` still runs once before a commit: it is
the only run that sees everything.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DIRS = ("tests", "plugins")
ENV = "TAVERN_AFFECTED"
# values on removed lines worth looking for in tests: numbers with a dot or of 3+ digits, strings of 4+ characters
_NUMBER = re.compile(r"(?<![\w.])(\d+\.\d+|\d{3,})(?![\w.])")
_STRING = re.compile(r"""(["'])([^"'\n]{4,80})\1""")
_COMMON = {"utf-8", "true", "false", "none", "name", "text", "content", "title", "mode", "rules", "always", "search"}


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return done.stdout if done.returncode == 0 else ""


def changed_files() -> list[str]:
    """Files changed against HEAD, and new files git does not ignore."""
    names = _git("diff", "--name-only", "HEAD").splitlines() + _git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({n.strip().replace("\\", "/") for n in names if n.strip()})


def module_of(path: str) -> str | None:
    """`tavern/core/retrieval.py` -> `tavern.core.retrieval`; None for files that are not Python modules."""
    if not path.endswith(".py") or not (path.startswith("tavern/") or path.startswith("plugins/")):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def test_files() -> list[Path]:
    found: list[Path] = []
    for folder in TEST_DIRS:
        base = ROOT / folder
        if base.is_dir():
            found.extend(p for p in base.rglob("test_*.py") if "__pycache__" not in p.parts)
    return sorted(found)


def imports_of(source: str) -> set[str]:
    """Every module a test file imports, `from a.b import c` counted as both `a.b` and `a.b.c`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def removed_values(path: str) -> set[str]:
    """Numbers and quoted strings on the lines a change removed from `path` (not on lines it kept)."""
    diff = _git("diff", "-U0", "HEAD", "--", path)
    removed = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    values: set[str] = set()
    for line in removed:
        if line.lstrip().startswith("#"):
            continue
        values.update(_NUMBER.findall(line))
        values.update(m.group(2) for m in _STRING.finditer(line) if m.group(2).strip().lower() not in _COMMON)
    return {v for v in values if v not in added}


def affected_tests(changed: list[str]) -> dict[str, list[str]]:
    """Test file (relative path) -> why it is affected."""
    tests = test_files()
    texts = {t: t.read_text(encoding="utf-8", errors="replace") for t in tests}
    imports = {t: imports_of(texts[t]) for t in tests}
    why: dict[str, list[str]] = {}

    def add(test: Path, reason: str) -> None:
        reasons = why.setdefault(test.relative_to(ROOT).as_posix(), [])
        if reason not in reasons:
            reasons.append(reason)

    for path in changed:
        full = ROOT / path
        name = Path(path).name
        if name.startswith("test_") and name.endswith(".py") and full.exists():
            add(full, "changed itself")
            continue
        module = module_of(path)
        stem = Path(path).stem
        for test in tests:
            if module and module in imports[test]:
                add(test, f"imports {module}")
            elif module and stem != "__init__" and len(stem) > 3 and (
                    test.stem.startswith(f"test_{stem}") or re.search(rf"\b{re.escape(stem)}\b", texts[test])):
                add(test, f"names {stem}")
            elif path.startswith("web/") and name in texts[test]:
                add(test, f"names {name}")
        if path.endswith(".py"):
            for value in sorted(removed_values(path)):
                for test in tests:
                    if value in texts[test]:
                        add(test, f"still has removed value {value!r} from {path}")
    return why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the everyday tests plus every test a change can break.")
    parser.add_argument("files", nargs="*", help="changed files (default: from git)")
    parser.add_argument("--list", action="store_true", help="only print the affected test files")
    args = parser.parse_args(argv)
    changed = [f.replace("\\", "/") for f in args.files] or changed_files()
    why = affected_tests(changed)
    print(f"changed: {len(changed)} files; affected test files: {len(why)}")
    for test, reasons in sorted(why.items()):
        print(f"  {test}  <- {'; '.join(reasons[:3])}")
    if args.list:
        return 0
    env = {**os.environ, ENV: os.pathsep.join(sorted(why))}
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-m", "fast or affected"]
    print("running:", " ".join(command[1:]), "(fast tier + affected)")
    done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(done.stdout[-3000:] + done.stderr[-1000:])  # the end of the run: failures and the summary line
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
