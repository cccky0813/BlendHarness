"""A tool = a description the model reads + a JSON Schema of its arguments + the Python function that runs."""

from dataclasses import dataclass
from typing import Any, Callable

# Permission tiers, from least to most risky. A tool declares what it can do, so an agent can be
# given only the tiers it needs (e.g. a reviewer that may look but not change anything).
PERMISSIONS = ("read", "write", "execute")   # read: observe state | write: change agent state | execute: run code


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
    permission: str = "read"               # one of PERMISSIONS

    def __post_init__(self) -> None:
        if self.permission not in PERMISSIONS:
            raise ValueError(f"tool {self.name!r}: permission must be one of {PERMISSIONS}, got {self.permission!r}")

    def schema(self) -> dict[str, Any]:
        """The format the chat-completions API expects in `tools=[...]`."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }
