"""get.sh must parse and be wrapped in a function called on the last line (it is read from a pipe)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GET = Path(__file__).resolve().parents[2] / "get.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_get_sh_parses_and_runs_from_a_function() -> None:
    assert subprocess.run(["bash", "-n", str(GET)], capture_output=True).returncode == 0
    lines = [ln for ln in GET.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines[-1] == 'main "$@"'
    assert any(ln.startswith("main()") for ln in lines)
