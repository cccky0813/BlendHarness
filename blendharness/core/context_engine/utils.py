"""Read-only helpers over a message history (lookups and size measurement)."""

from blendharness.core.context_engine.token import message_chars
from blendharness.core.context_engine.types import Messages


def tool_result_for(messages: Messages, call_id: str) -> str:
    """The content of the tool result that answers tool call `call_id` ('' if none yet)."""
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
            return str(m.get("content", ""))
    return ""


def size_chars(messages: Messages) -> int:
    """Characters of text (incl. tool-call arguments) in a message list."""
    return sum(message_chars(m)[0] for m in messages)
