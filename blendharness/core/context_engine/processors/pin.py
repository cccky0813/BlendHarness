"""Pin processor: keep the up-to-date version of something always visible at the end of the view."""

from typing import Callable, Optional

from blendharness.core.context_engine.processors.base import Processor
from blendharness.core.context_engine.types import Messages


def pin_at_end(messages: Messages, text: str) -> Messages:
    """Append a 'pinned' message that always shows the up-to-date version of something the model works
    on (e.g. the current script). It lives only in the view, never in the stored history, and sits at the
    END so the earlier prefix stays unchanged (friendlier to prompt caching than rewriting old messages)."""
    return messages + [{"role": "user", "content": text}]


class Pin(Processor):
    """Pin whatever `provider()` returns; nothing when it returns None (e.g. no working script yet)."""

    def __init__(self, provider: Callable[[], Optional[str]]) -> None:
        self.provider = provider

    def __call__(self, messages: Messages) -> Messages:
        text = self.provider()
        return pin_at_end(messages, text) if text is not None else messages
