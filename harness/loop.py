"""The agent loop: the model decides, the harness executes.

    call model -> tool calls? -> run each tool -> feed results back -> call model again ...
    until the model answers without a tool call, or a budget is exhausted.

Tool errors are NOT fatal: they are sent back to the model so it can fix them itself.
The loop never decides what to do after failing; it stops and reports why (LoopResult),
and the caller (orchestrator / eval runner / user session) chooses the fallback.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from harness.llm import LLMClient, ToolCall

MAX_TOOL_OUTPUT_CHARS = 6000  # keep one huge tool output from flooding the context


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str                       # the model reads this to decide when to use the tool
    parameters: dict[str, Any]             # JSON Schema of the arguments
    fn: Callable[..., ToolResult | str]    # the Python function that does the work

    def schema(self) -> dict[str, Any]:
        """The format the chat-completions API expects in `tools=[...]`."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass
class LoopResult:
    status: str                  # "done" | "max_steps" | "stuck" | "over_budget" | "truncated"
    final_text: str              # the model's last answer (may be empty if it never finished)
    steps: int
    cost_usd: float
    reason: str = ""             # human-readable explanation when status != "done"
    messages: list[dict[str, Any]] = field(default_factory=list)  # full history, for the caller


class AgentLoop:
    def __init__(
        self,
        name: str,
        llm: LLMClient,
        system_prompt: str,
        tools: list[Tool],
        max_steps: int = 20,
        max_same_error: int = 3,
        max_cost_usd: Optional[float] = None,
    ) -> None:
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in tools}
        self.max_steps = max_steps
        self.max_same_error = max_same_error
        self.max_cost_usd = max_cost_usd

    def run(self, task: str) -> LoopResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        tool_schemas = [t.schema() for t in self.tools.values()]
        error_counts: dict[str, int] = {}
        cost = 0.0
        truncations = 0

        with self.llm.tracer.span("agent", self.name, task=task[:300]) as span:
            for step in range(1, self.max_steps + 1):
                resp = self.llm.chat(messages, tools=tool_schemas or None, agent=self.name)
                cost += resp.cost_usd or 0.0
                messages.append(resp.raw_message)  # the model's own turn, incl. its tool calls

                if not resp.tool_calls and resp.finish_reason == "length":
                    # Cut off by max_tokens (often by over-long thinking): NOT a final answer.
                    truncations += 1
                    if truncations >= 2:
                        return self._finish(span, "truncated", resp.text, step, cost, messages,
                                            reason="output hit max_tokens twice in a row")
                    messages.append({"role": "user", "content":
                                     "Your last response was cut off by the output length limit before you "
                                     "acted. Stop deliberating and act now: call the appropriate tool."})
                    continue
                truncations = 0

                if not resp.tool_calls:  # no tool requested: the model considers the task finished
                    return self._finish(span, "done", resp.text, step, cost, messages)

                for i, call in enumerate(resp.tool_calls):
                    result = self._execute(call)
                    # id and result come from the same `call` -> they can never be mismatched
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result.content})

                    if result.is_error:
                        signature = f"{call.name}: {result.content.strip().splitlines()[0][:200]}"
                        error_counts[signature] = error_counts.get(signature, 0) + 1
                        if error_counts[signature] >= self.max_same_error:
                            # Every tool call must get a result, or the history becomes invalid
                            # for anyone who continues it (the API rejects unanswered calls).
                            for skipped in resp.tool_calls[i + 1:]:
                                messages.append({"role": "tool", "tool_call_id": skipped.id,
                                                 "content": "SKIPPED: agent stopped before running this call."})
                            return self._finish(span, "stuck", resp.text, step, cost, messages,
                                                reason=f"same error {self.max_same_error}x: {signature}")

                if self.max_cost_usd is not None and cost >= self.max_cost_usd:
                    return self._finish(span, "over_budget", resp.text, step, cost, messages,
                                        reason=f"spent ${cost:.4f} >= limit ${self.max_cost_usd}")

            return self._finish(span, "max_steps", "", self.max_steps, cost, messages,
                                reason=f"no final answer after {self.max_steps} steps")

    def _execute(self, call: ToolCall) -> ToolResult:
        """Run one tool call. Every failure becomes an error message the model can read."""
        with self.llm.tracer.span("tool", call.name, args=call.arguments) as span:
            tool = self.tools.get(call.name)
            if tool is None:
                result = ToolResult(f"ERROR: unknown tool '{call.name}'. Available: {list(self.tools)}", True)
            elif "__invalid_json__" in call.arguments:
                result = ToolResult(f"ERROR: arguments were not valid JSON: {call.arguments['__invalid_json__']}", True)
            else:
                try:
                    out = tool.fn(**call.arguments)
                    result = out if isinstance(out, ToolResult) else ToolResult(str(out))
                except Exception as exc:  # a crashing tool must not crash the agent
                    result = ToolResult(f"ERROR: {type(exc).__name__}: {exc}", True)

            if len(result.content) > MAX_TOOL_OUTPUT_CHARS:
                result.content = result.content[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
            span.set(is_error=result.is_error, output_preview=result.content[:300])
            return result

    def _finish(self, span, status, text, steps, cost, messages, reason="") -> LoopResult:
        span.set(status=status, steps=steps, cost_usd=round(cost, 6), reason=reason)
        return LoopResult(status, text, steps, cost, reason, messages)
