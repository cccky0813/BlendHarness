"""Tool registry: the tools one agent may use, looked up by name and executed safely.

Execution follows the error-as-observation rule: an unknown tool, invalid arguments or a crashing tool
never crash the agent; they become an error message the model can read and react to.
"""

from typing import Iterable, Iterator, Optional

from blendharness.core.llm import ToolCall
from blendharness.core.tools.base import PERMISSIONS, Tool, ToolResult
from blendharness.core.tracing import Tracer

MAX_TOOL_OUTPUT_CHARS = 6000  # keep one huge tool output from flooding the context


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name {tool.name!r}")  # the model could not tell them apart
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(list(self._tools.values()))

    def __len__(self) -> int:
        return len(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def select(self, max_permission: str = "execute", exclude: Iterable[str] = ()) -> "ToolRegistry":
        """A narrower registry: only tools up to `max_permission`, minus the excluded names."""
        limit = PERMISSIONS.index(max_permission)
        skip = set(exclude)
        return ToolRegistry(t for t in self._tools.values()
                            if PERMISSIONS.index(t.permission) <= limit and t.name not in skip)

    def execute(self, call: ToolCall, tracer: Tracer) -> ToolResult:
        """Run one tool call inside a "tool" span. Every failure becomes an error message the model can read."""
        tool = self._tools.get(call.name)
        with tracer.span("tool", call.name, args=call.arguments,
                         permission=tool.permission if tool else None) as span:
            if tool is None:
                result = ToolResult(f"ERROR: unknown tool '{call.name}'. Available: {self.names()}", True)
            elif "__invalid_json__" in call.arguments:
                result = ToolResult(f"ERROR: arguments were not valid JSON: {call.arguments['__invalid_json__']}", True)
            else:
                try:
                    out = tool.fn(**call.arguments)
                    result = out if isinstance(out, ToolResult) else ToolResult(str(out))
                except Exception as exc:  # a crashing tool must not crash the agent
                    result = ToolResult(f"ERROR: {type(exc).__name__}: {exc}", True)

            if len(result.content) > MAX_TOOL_OUTPUT_CHARS:
                hidden = len(result.content) - MAX_TOOL_OUTPUT_CHARS
                result.content = (result.content[:MAX_TOOL_OUTPUT_CHARS]
                                  + f"\n...[truncated: {hidden} more chars not shown]")
            span.set(is_error=result.is_error, output_preview=result.content[:300])
            return result
