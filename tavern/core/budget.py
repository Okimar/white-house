"""Limits for one generation run: model calls, tokens, cost, wall time.

Every model call made through `LLMClient` charges the run's budget. When a limit is hit the
call raises `BudgetExceeded` and the run ends with a clear error instead of burning money.
Plugins that call the model many times (agent trees) rely on this.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from tavern.errors import TavernError


class BudgetExceeded(TavernError):
    """A run limit was reached. `limit` names which one."""

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


@dataclass
class RunBudget:
    max_steps: int = 50
    max_tokens: int = 0  # 0 = unlimited. Total prompt + completion tokens across all calls.
    max_cost: float = 0.0  # 0 = unlimited. Currency units, only counted when prices are known.
    timeout: float = 0.0  # 0 = unlimited. Seconds since the budget was created.

    steps: int = 0
    tokens: int = 0
    cost: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def begin_step(self) -> None:
        """Call before every model request."""
        if self.max_steps and self.steps >= self.max_steps:
            raise BudgetExceeded("steps", f"Step limit reached ({self.max_steps} model calls)")
        if self.timeout and time.monotonic() - self.started > self.timeout:
            raise BudgetExceeded("timeout", f"Time limit reached ({self.timeout:g}s)")
        self.check_totals()
        self.steps += 1

    def charge(self, prompt_tokens: int = 0, completion_tokens: int = 0, cost: float = 0.0) -> None:
        self.tokens += prompt_tokens + completion_tokens
        self.cost += cost

    def check_totals(self) -> None:
        if self.max_tokens and self.tokens >= self.max_tokens:
            raise BudgetExceeded("tokens", f"Token limit reached ({self.max_tokens})")
        if self.max_cost and self.cost >= self.max_cost:
            raise BudgetExceeded("cost", f"Cost limit reached ({self.max_cost:g})")

    def snapshot(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "cost": round(self.cost, 6),
            "max_steps": self.max_steps,
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
        }
