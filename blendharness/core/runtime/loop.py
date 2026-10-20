"""The agent loop: the model decides, the harness executes.

    call model -> tool calls? -> run each tool -> feed results back -> call model again ...
    until the model answers without a tool call, or a rail stops the run.

The loop is pure mechanism: call the model, run tools, keep the history valid, report the outcome.
Every policy ("is it really finished?", "is it stuck?", "too expensive?") lives in a rail
(core/rails) that the loop consults at fixed events.

Tool errors are NOT fatal: they are sent back to the model so it can fix them itself.
The loop never decides what to do after failing; it stops and reports why (LoopResult),
and the caller (orchestrator / eval runner / user session) chooses the fallback.
"""

from typing import Any, Optional

from blendharness.core.context_engine import ContextPolicy, estimate_tokens
from blendharness.core.llm import LLMClient, ToolCall
from blendharness.core.rails import CompletionCheck, CompletionRail, CostBudgetRail, Rail, StuckRail, TruncationRail
from blendharness.core.rails.events import Decision, LoopContext, Nudge, Stop
from blendharness.core.runtime.types import LoopResult
from blendharness.core.tools import Tool, ToolRegistry


class AgentLoop:
    def __init__(
        self,
        name: str,
        llm: LLMClient,
        system_prompt: str,
        tools: list[Tool] | ToolRegistry,
        max_steps: int = 20,                   # hard limit of the loop itself
        # Shortcuts for the standard rails (truncation and stuck are always on):
        max_same_error: int = 3,
        max_cost_usd: Optional[float] = None,
        completion_check: Optional[CompletionCheck] = None,
        context_policy: Optional[ContextPolicy] = None,  # full history -> what the model sees
        rails: Optional[list[Rail]] = None,    # extra rails, added to the standard ones
    ) -> None:
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.tools = tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools)
        self.max_steps = max_steps
        self.context_policy = context_policy
        standard: list[Rail] = [TruncationRail(), StuckRail(max_same_error)]
        if max_cost_usd is not None:
            standard.append(CostBudgetRail(max_cost_usd))
        if completion_check is not None:
            standard.append(CompletionRail(completion_check))
        self.rails = sorted(standard + list(rails or []), key=lambda r: r.priority)  # stable: ties keep order

    def run(self, task: str) -> LoopResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        tool_schemas = self.tools.schemas()

        with self.llm.tracer.span("agent", self.name, task=task[:300]) as span:
            ctx = LoopContext(self.name, messages, span)
            for rail in self.rails:
                rail.before_run(ctx)

            for step in range(1, self.max_steps + 1):
                ctx.step = step
                # The model sees a compacted view; `messages` itself keeps the full history.
                view = self.context_policy(messages) if self.context_policy else messages
                if view is not messages:  # a ContextEngine explains what each processor did
                    report = getattr(self.context_policy, "last_report", "") or \
                        f"{estimate_tokens(messages)} -> {estimate_tokens(view)} tok"
                    span.set(**{f"context_step{step}": report})
                resp = self.llm.chat(view, tools=tool_schemas or None, agent=self.name)
                ctx.cost_usd += resp.cost_usd or 0.0
                messages.append(resp.raw_message)  # the model's own turn, incl. its tool calls

                decision = self._ask("after_llm", ctx, resp)
                if decision is None and not resp.tool_calls:
                    decision = self._ask("on_final_answer", ctx, resp)
                    if decision is None:
                        return self._finish(ctx, "done", resp.text)
                if decision is not None:
                    self._skip(messages, resp.tool_calls)
                    if isinstance(decision, Stop):
                        return self._finish(ctx, decision.status, resp.text, decision.reason)
                    messages.append({"role": "user", "content": decision.message})
                    continue

                for i, call in enumerate(resp.tool_calls):
                    result = self.tools.execute(call, self.llm.tracer)
                    # id and result come from the same `call` -> they can never be mismatched
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result.content})
                    decision = self._ask("after_tool", ctx, call, result)
                    if decision is not None:
                        self._skip(messages, resp.tool_calls[i + 1:])
                        break
                decision = decision or self._ask("after_step", ctx)
                if isinstance(decision, Stop):
                    return self._finish(ctx, decision.status, resp.text, decision.reason)
                if isinstance(decision, Nudge):
                    messages.append({"role": "user", "content": decision.message})

            return self._finish(ctx, "max_steps", "", f"no final answer after {self.max_steps} steps")

    def _ask(self, event: str, ctx: LoopContext, *args) -> Decision:
        """Consult the rails in priority order; the first decision wins."""
        for rail in self.rails:
            decision = getattr(rail, event)(ctx, *args)
            if decision is not None:
                ctx.span.set(**{f"rail_step{ctx.step}": f"{rail.name}.{event} -> {decision}"})
                return decision
        return None

    @staticmethod
    def _skip(messages: list[dict[str, Any]], calls: list[ToolCall]) -> None:
        """Every tool call must get a result, or the history becomes invalid for anyone who
        continues it (the API rejects unanswered calls)."""
        for call in calls:
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": "SKIPPED: agent stopped before running this call."})

    @staticmethod
    def _finish(ctx: LoopContext, status: str, text: str, reason: str = "") -> LoopResult:
        ctx.span.set(status=status, steps=ctx.step, cost_usd=round(ctx.cost_usd, 6), reason=reason)
        return LoopResult(status, text, ctx.step, ctx.cost_usd, reason, ctx.messages)
