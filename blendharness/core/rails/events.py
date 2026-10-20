"""What the loop tells rails (LoopContext) and what rails can answer (a Decision).

At each event the loop asks its rails in priority order; the first one that returns a decision wins:
    None         carry on
    Nudge(msg)   tell the model something and give it another step (e.g. "you are not finished")
    Stop(...)    end the run with this status and reason (e.g. "stuck", "over_budget")
"""

from dataclasses import dataclass
from typing import Any, Optional

from blendharness.core.tracing import Span


@dataclass
class LoopContext:
    """The state of one run that rails may read (and annotate the trace with, via `span`)."""
    agent: str
    messages: list[dict[str, Any]]   # full history, including the latest reply
    span: Span                       # the run's "agent" span
    step: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class Nudge:
    message: str


@dataclass(frozen=True)
class Stop:
    status: str                      # becomes LoopResult.status
    reason: str


Decision = Optional[Nudge | Stop]
