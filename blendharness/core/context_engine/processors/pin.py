"""Pin processor: keep the up-to-date version of something always visible at the end of the view."""

from blendharness.core.context_engine.types import Messages


def pin_at_end(messages: Messages, text: str) -> Messages:
    """Append a 'pinned' message that always shows the up-to-date version of something the model works
    on (e.g. the current script). It lives only in the view, never in the stored history, and sits at the
    END so the earlier prefix stays unchanged (friendlier to prompt caching than rewriting old messages)."""
    return messages + [{"role": "user", "content": text}]
