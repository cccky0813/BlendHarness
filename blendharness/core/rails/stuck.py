"""Stuck: the same tool error N times means the model is going in circles, so stop."""

from blendharness.core.llm import ToolCall
from blendharness.core.rails.base import Rail
from blendharness.core.rails.events import Decision, LoopContext, Stop
from blendharness.core.tools import ToolResult


class StuckRail(Rail):
    priority = 10

    def __init__(self, max_same_error: int = 3) -> None:
        self.max_same_error = max_same_error
        self.counts: dict[str, int] = {}

    def before_run(self, ctx: LoopContext) -> None:
        self.counts = {}

    def after_tool(self, ctx: LoopContext, call: ToolCall, result: ToolResult) -> Decision:
        if not result.is_error:
            return None
        signature = f"{call.name}: {result.content.strip().splitlines()[0][:200]}"
        self.counts[signature] = self.counts.get(signature, 0) + 1
        if self.counts[signature] >= self.max_same_error:
            return Stop("stuck", f"same error {self.max_same_error}x: {signature}")
        return None
