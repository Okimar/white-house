"""Every browser module parses: one bad character (a raw line break in a string) blanks the whole page.

Needs Node.js; skipped where it is missing (Termux, plain installs). Regression for stage 10е-1, where
the preset editor shipped a string broken over lines and the page stayed empty. One test checks all the
modules at once (in threads: a Node start costs more than the parse) and names every broken file.
"""

from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")
PLUGINS = Path(__file__).resolve().parents[2] / "tavern" / "builtin_plugins"
FILES = sorted([p for p in WEB.rglob("*.js") if "vendor" not in p.parts] + list(PLUGINS.glob("*/*.js")))


def _check(path: Path) -> str | None:
    result = subprocess.run([NODE, "--input-type=module", "--check"], input=path.read_bytes(),
                            capture_output=True, timeout=60)
    if result.returncode == 0:
        return None
    return f"{path.name}: {result.stderr.decode('utf-8', 'replace')[:400]}"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_every_browser_module_parses() -> None:
    assert FILES
    with ThreadPoolExecutor(max_workers=8) as pool:
        broken = [message for message in pool.map(_check, FILES) if message]
    assert not broken, "\n".join(broken)
