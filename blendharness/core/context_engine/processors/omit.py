"""Omit processors: replace stale copies of large content with short placeholders (on a COPY of the history)."""

import copy
import json
import re
from typing import Callable

from blendharness.core.context_engine.processors.base import Processor
from blendharness.core.context_engine.types import Messages


def omit_large_tool_args(messages: Messages, keep_last: int = 1, min_chars: int = 1500,
                         keep: Callable[[Messages, dict], bool] | None = None) -> Messages:
    """Replace big string arguments of older tool calls (e.g. full scripts) with a short placeholder.

    The `keep_last` most recent large calls stay intact, plus any call for which `keep(messages, call)`
    is True (e.g. a failed script the model still has to fix).
    """
    out = copy.deepcopy(messages)
    large = []  # (message index, tool-call index) of calls with large arguments, oldest first
    for mi, m in enumerate(out):
        for ci, tc in enumerate(m.get("tool_calls") or []):
            if len(tc["function"].get("arguments") or "") >= min_chars:
                large.append((mi, ci))
    to_omit = large[:-keep_last] if keep_last else large
    if keep:
        to_omit = [(mi, ci) for mi, ci in to_omit if not keep(out, out[mi]["tool_calls"][ci])]
    for version, (mi, ci) in enumerate(to_omit, start=1):
        fn = out[mi]["tool_calls"][ci]["function"]
        try:
            args = json.loads(fn["arguments"])
        except json.JSONDecodeError:
            continue
        for key, value in args.items():
            if isinstance(value, str) and len(value) >= min_chars:
                args[key] = (f"<omitted: version {version} of this {key}, {len(value)} chars, superseded by a "
                             f"later call. Use view_script to see the current version.>")
        fn["arguments"] = json.dumps(args, ensure_ascii=False)
    return out


_CODE_BLOCK = re.compile(r"```python\n.*?```", re.DOTALL)


def omit_stale_code_blocks(messages: Messages, is_superseded: Callable[[Messages, int], bool],
                           min_chars: int = 1500) -> Messages:
    """Replace large ```python blocks in user messages once `is_superseded(messages, index)` says
    a newer version exists later in the history (e.g. the script given in a revision task)."""
    out = copy.deepcopy(messages)
    for i, m in enumerate(out):
        if m.get("role") != "user" or not isinstance(m.get("content"), str):
            continue
        if not is_superseded(out, i):
            continue
        m["content"] = _CODE_BLOCK.sub(
            lambda match: match.group(0) if len(match.group(0)) < min_chars else
            f"```python\n# <omitted: {len(match.group(0))} chars, outdated; the script has been changed "
            f"since. Use view_script to see the current version.>\n```",
            m["content"],
        )
    return out


# ---------- as pipeline steps ----------
class OmitLargeToolArgs(Processor):
    """Older copies of large tool arguments (e.g. full scripts) become placeholders."""

    def __init__(self, keep_last: int = 1, min_chars: int = 1500,
                 keep: Callable[[Messages, dict], bool] | None = None) -> None:
        self.keep_last, self.min_chars, self.keep = keep_last, min_chars, keep

    def __call__(self, messages: Messages) -> Messages:
        return omit_large_tool_args(messages, self.keep_last, self.min_chars, self.keep)


class OmitStaleCodeBlocks(Processor):
    """Large ```python blocks in user messages become placeholders once superseded."""

    def __init__(self, is_superseded: Callable[[Messages, int], bool], min_chars: int = 1500) -> None:
        self.is_superseded, self.min_chars = is_superseded, min_chars

    def __call__(self, messages: Messages) -> Messages:
        return omit_stale_code_blocks(messages, self.is_superseded, self.min_chars)
