"""Runs regex jobs in a worker process that can be stopped (stage 11; see core/regex_worker.py and ADR 0017).

One worker lives as long as the app; it starts on the first job (a chat without rules never starts it).
A job is sent as one line of JSON and the worker reports "R <rule>" before each rule. If no word comes
within RULE_SECONDS, or the whole job takes more than JOB_SECONDS, the worker is killed, the rule it was
on is named, and the job is run again without that rule. The caller learns which rules were skipped.

`run()` blocks (a pipe, a lock): call it from a thread (`asyncio.to_thread`) where the event loop matters;
a normal job takes a few milliseconds.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

WORKER = Path(__file__).with_name("regex_worker.py")
RULE_SECONDS = float(os.environ.get("TAVERN_REGEX_RULE_SECONDS", "2.0"))
JOB_SECONDS = float(os.environ.get("TAVERN_REGEX_JOB_SECONDS", "10.0"))
MAX_ROUNDS = 8  # how many times a job is run again after a rule was skipped
MAX_JOB_CHARS = 20_000_000


@dataclass
class Outcome:
    outs: list[str]
    hits: list[list[int]]
    skipped: dict[int, str] = field(default_factory=dict)  # rule index -> "slow" | the engine's error text
    matches: list[dict[str, Any]] = field(default_factory=list)


class _Worker:
    def __init__(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-S", str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=flags)
        self.lines: queue.Queue[bytes | None] = queue.Queue()  # answers only
        self.last: int | None = None   # the rule the worker said it started on (progress lines never queue up:
        self.last_at = time.monotonic()  # a thousand rules would be a thousand thread hand-offs)
        threading.Thread(target=self._read, daemon=True, name="regex-worker-reader").start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in iter(self.proc.stdout.readline, b""):
            if line.startswith(b"R "):
                self.last = int(line[2:])
                self.last_at = time.monotonic()
            else:
                self.lines.put(line)
        self.lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - already gone
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass


class Runner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker: _Worker | None = None

    def close(self) -> None:
        with self._lock:
            if self._worker:
                self._worker.kill()
                self._worker = None

    def _get(self) -> _Worker:
        if self._worker is None or not self._worker.alive():
            self._worker = _Worker()
        return self._worker

    def _once(self, specs: list[dict[str, Any]], items: list[dict[str, Any]], find: bool,
              rule_seconds: float, job_seconds: float) -> tuple[dict[str, Any] | None, int | None]:
        """(answer, None) or (None, rule that hung or None when nothing answered at all)."""
        # Only the rules some item uses go over the pipe (a thousand rules, a few of them for this text).
        used = sorted({r for item in items for r in item["rules"]})
        new = {old: n for n, old in enumerate(used)}
        sent = [{"text": i["text"], "rules": [new[r] for r in i["rules"]]} for i in items]
        payload = json.dumps({"specs": [specs[r] for r in used], "items": sent, "find": find}, ensure_ascii=True,
                             separators=(",", ":")).encode("ascii") + b"\n"
        for attempt in (0, 1):
            worker = self._get()
            started = worker.last_at = time.monotonic()
            worker.last = None
            try:
                assert worker.proc.stdin is not None
                worker.proc.stdin.write(payload)
                worker.proc.stdin.flush()
                break
            except (OSError, ValueError):
                worker.kill()
                self._worker = None
                if attempt:
                    return None, None
        while True:
            deadline = min(worker.last_at + rule_seconds, started + job_seconds)
            try:
                line = worker.lines.get(timeout=max(deadline - time.monotonic(), 0.001))
            except queue.Empty:
                if time.monotonic() < min(worker.last_at + rule_seconds, started + job_seconds):
                    continue  # the worker moved on to another rule meanwhile
                last = worker.last
                worker.kill()
                self._worker = None
                return None, None if last is None else used[last]
            if line is None:  # the process died
                last = worker.last
                worker.kill()
                self._worker = None
                return None, None if last is None else used[last]
            answer = json.loads(line)
            if "errors" in answer:
                answer["errors"] = {str(used[int(k)]): v for k, v in answer["errors"].items()}
            if "hits" in answer:
                answer["hits"] = [[used[h] for h in hits] for hits in answer["hits"]]
            return answer, None

    def run(self, specs: list[dict[str, Any]], items: list[dict[str, Any]], *, find: bool = False,
            rule_seconds: float | None = None, job_seconds: float | None = None) -> Outcome:
        """Apply `specs` to `items` ({text, rules: [spec indexes in order]}). Rules that hang are skipped."""
        rule_seconds = RULE_SECONDS if rule_seconds is None else rule_seconds
        job_seconds = JOB_SECONDS if job_seconds is None else job_seconds
        if sum(len(i["text"]) for i in items) > MAX_JOB_CHARS:
            raise ValueError("too much text for one job")
        skipped: dict[int, str] = {}
        with self._lock:
            for _ in range(MAX_ROUNDS):
                live = [{"text": i["text"], "rules": [r for r in i["rules"] if r not in skipped]} for i in items]
                answer, hung = self._once(specs, live, find, rule_seconds, job_seconds)
                if answer is None:
                    if hung is None:  # nothing answered: give up on every rule rather than loop
                        for item in items:
                            for r in item["rules"]:
                                skipped.setdefault(r, "engine")
                        return Outcome([i["text"] for i in items], [[] for _ in items], skipped)
                    skipped[hung] = "slow"
                    log.warning("Regex rule %s took too long and was skipped", hung)
                    continue
                if answer.get("failed"):
                    log.warning("Regex worker failed: %s", answer["failed"])
                    return Outcome([i["text"] for i in items], [[] for _ in items],
                                   {r: "engine" for i in items for r in i["rules"]})
                for key, text in answer.get("errors", {}).items():
                    skipped[int(key)] = text
                return Outcome(answer["outs"], answer["hits"], skipped, answer.get("matches", []))
        return Outcome([i["text"] for i in items], [[] for _ in items], skipped)


RUNNER = Runner()
atexit.register(RUNNER.close)
