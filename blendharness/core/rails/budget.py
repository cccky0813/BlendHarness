"""Cost budget: stop once the run has spent its allowance (checked after the step's tools ran)."""

from blendharness.core.rails.base import Rail
from blendharness.core.rails.events import Decision, LoopContext, Stop


class CostBudgetRail(Rail):
    priority = 10

    def __init__(self, max_cost_usd: float) -> None:
        self.max_cost_usd = max_cost_usd

    def after_step(self, ctx: LoopContext) -> Decision:
        if ctx.cost_usd >= self.max_cost_usd:
            return Stop("over_budget", f"spent ${ctx.cost_usd:.4f} >= limit ${self.max_cost_usd}")
        return None
