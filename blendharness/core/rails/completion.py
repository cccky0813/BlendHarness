"""Completion: don't take "I'm done" on faith; verify the agent's completion condition."""

from typing import Any, Callable, Optional

from blendharness.core.llm import LLMResponse
from blendharness.core.rails.base import Rail
from blendharness.core.rails.events import Decision, LoopContext, Nudge, Stop

# messages -> None if really finished, else a reason the model is told before it continues
CompletionCheck = Callable[[list[dict[str, Any]]], Optional[str]]


class CompletionRail(Rail):
    priority = 50

    def __init__(self, check: CompletionCheck, max_nudges: int = 2) -> None:
        self.check = check
        self.max_nudges = max_nudges
        self.nudges = 0

    def before_run(self, ctx: LoopContext) -> None:
        self.nudges = 0

    def on_final_answer(self, ctx: LoopContext, resp: LLMResponse) -> Decision:
        problem = self.check(ctx.messages)
        if not problem:
            return None
        if self.nudges < self.max_nudges:
            self.nudges += 1
            ctx.span.set(**{f"completion_rejected_{self.nudges}": problem})
            return Nudge(f"Not finished yet: {problem}")
        return Stop("unverified", problem)   # finished without meeting the condition: the caller decides
