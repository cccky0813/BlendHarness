"""Token estimate for a message list, without a tokenizer (Claude's tokenizer is not public).

Calibrated on our own traces (692 Claude calls: payload size vs. the provider's reported input_tokens):
  - 2.32 characters per token (p10-p90: 2.1-2.65) for text + tool schemas. Our context is mostly code,
    which tokenizes much denser than prose: the usual "4 chars per token" would undercount by ~40%.
  - ~330 tokens per 512 px image (Anthropic's formula w*h/750 gives 349).
The estimate errs on the high side (2.3 chars/token, 350 per image), which is the safe side for a budget.
"""

from typing import Any

from blendharness.core.context_engine.types import Messages

CHARS_PER_TOKEN = 2.3
TOKENS_PER_IMAGE = 350


def message_chars(m: dict[str, Any]) -> tuple[int, int]:
    """(characters of text incl. tool-call arguments, number of images) of one message."""
    chars = images = 0
    content = m.get("content")
    if isinstance(content, str):
        chars += len(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                images += 1
            elif isinstance(part, dict):
                chars += len(part.get("text", ""))
    chars += sum(len(tc["function"].get("arguments") or "") for tc in m.get("tool_calls") or [])
    return chars, images


def estimate_tokens(messages: Messages) -> int:
    chars = images = 0
    for m in messages:
        c, i = message_chars(m)
        chars += c
        images += i
    return round(chars / CHARS_PER_TOKEN) + images * TOKENS_PER_IMAGE
