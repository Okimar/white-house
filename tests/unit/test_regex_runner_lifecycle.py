"""The persistent regex worker must never outlive its owner or keep a process from exiting."""

import subprocess
import sys
import time

from tavern.core.regex import build_spec
from tavern.core.regex_runner import Runner
from tavern.paths import REPO_ROOT

SPEC = build_spec({"find": "/a/g", "replace": "b"})[0]


def test_worker_ends_when_runner_is_closed():
    runner = Runner()
    try:
        assert runner.run([SPEC], [{"text": "aaa", "rules": [0]}]).outs == ["bbb"]
        worker = runner._worker
        assert worker is not None and worker.alive()
    finally:
        runner.close()
    assert worker.proc.poll() is not None
    assert runner._worker is None


def test_worker_ends_when_its_stdin_closes():
    """The parent dying closes the pipe: the worker has to notice and leave."""
    runner = Runner()
    try:
        runner.run([SPEC], [{"text": "aaa", "rules": [0]}])
        worker = runner._worker
        worker.proc.stdin.close()
        worker.proc.wait(timeout=5)
    finally:
        runner.close()


def test_a_process_that_used_the_runner_exits_by_itself():
    code = ("from tavern.core.regex_runner import RUNNER\n"
            "RUNNER.run([{'pattern':'a','flags':'g','replace':'b'}],[{'text':'aaa','rules':[0]}])\n")
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, timeout=30, capture_output=True)
    assert done.returncode == 0, done.stderr
    assert time.monotonic() - started < 20
