"""What one agent run returns to its caller."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopResult:
    status: str                  # "done" | "unverified" | "max_steps" | "stuck" | "over_budget" | "truncated"
    final_text: str              # the model's last answer (may be empty if it never finished)
    steps: int
    cost_usd: float
    reason: str = ""             # human-readable explanation when status != "done"
    messages: list[dict[str, Any]] = field(default_factory=list)  # full history, for the caller
