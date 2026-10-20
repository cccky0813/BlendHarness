"""Read-only helpers over a message history (lookups and size measurement)."""

from blendharness.core.context_engine.types import Messages


def tool_result_for(messages: Messages, call_id: str) -> str:
    """The content of the tool result that answers tool call `call_id` ('' if none yet)."""
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
            return str(m.get("content", ""))
    return ""


def size_chars(messages: Messages) -> int:
    """Rough context size (characters of text) for tracing the effect of compaction."""
    total = 0
    for m in messages:
        c = m.get("content")
        total += len(c) if isinstance(c, str) else sum(len(p.get("text", "")) for p in c or [] if isinstance(p, dict))
        total += sum(len(tc["function"].get("arguments") or "") for tc in m.get("tool_calls") or [])
    return total
