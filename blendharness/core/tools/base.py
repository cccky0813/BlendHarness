"""A tool = a description the model reads + a JSON Schema of its arguments + the Python function that runs."""

from dataclasses import dataclass
from typing import Any, Callable


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
