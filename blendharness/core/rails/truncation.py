"""Truncation: a reply cut off by max_tokens (often by over-long thinking) is NOT a final answer."""

from blendharness.core.llm import LLMResponse
from blendharness.core.rails.base import Rail
from blendharness.core.rails.events import Decision, LoopContext, Nudge, Stop

NUDGE = ("Your last response was cut off by the output length limit before you "
         "acted. Stop deliberating and act now: call the appropriate tool.")


class TruncationRail(Rail):
    priority = 10                # must run before completion: a cut-off reply is not "I'm done"

    def __init__(self, max_in_a_row: int = 2) -> None:
        self.max_in_a_row = max_in_a_row
        self.count = 0

    def before_run(self, ctx: LoopContext) -> None:
        self.count = 0

    def after_llm(self, ctx: LoopContext, resp: LLMResponse) -> Decision:
        if resp.tool_calls or resp.finish_reason != "length":
            self.count = 0       # only consecutive truncations count
            return None
        self.count += 1
        if self.count >= self.max_in_a_row:
            return Stop("truncated", "output hit max_tokens twice in a row")
        return Nudge(NUDGE)
