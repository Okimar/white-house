"""Every browser module parses: one bad character (a raw line break in a string) blanks the whole page.

Needs Node.js; skipped where it is missing (Termux, plain installs). Regression for stage 10е-1, where
the preset editor shipped a string broken over lines and the page stayed empty.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")
FILES = sorted(p for p in WEB.rglob("*.js") if "vendor" not in p.parts)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(WEB)))
def test_browser_module_parses(path: Path) -> None:
    result = subprocess.run([NODE, "--input-type=module", "--check"], input=path.read_bytes(),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
