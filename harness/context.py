"""Context management: what the model SEES each turn, without losing the full history.

The loop keeps the complete message history (for logs, debugging and training data).
A context policy builds a compacted COPY of it right before every model call.

Trade-off: rewriting an earlier message changes the prompt prefix, so provider-side prompt
caching cannot reuse that prefix any more. Compacting each block once (when it becomes stale)
keeps the cache misses rare.
"""

import copy
import json
import re
from typing import Any, Callable

Messages = list[dict[str, Any]]
ContextPolicy = Callable[[Messages], Messages]


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


def tool_result_for(messages: Messages, call_id: str) -> str:
    """The content of the tool result that answers tool call `call_id` ('' if none yet)."""
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
            return str(m.get("content", ""))
    return ""


def pin_at_end(messages: Messages, text: str) -> Messages:
    """Append a 'pinned' message that always shows the up-to-date version of something the model works
    on (e.g. the current script). It lives only in the view, never in the stored history, and sits at the
    END so the earlier prefix stays unchanged (friendlier to prompt caching than rewriting old messages)."""
    return messages + [{"role": "user", "content": text}]


def size_chars(messages: Messages) -> int:
    """Rough context size (characters of text) for tracing the effect of compaction."""
    total = 0
    for m in messages:
        c = m.get("content")
        total += len(c) if isinstance(c, str) else sum(len(p.get("text", "")) for p in c or [] if isinstance(p, dict))
        total += sum(len(tc["function"].get("arguments") or "") for tc in m.get("tool_calls") or [])
    return total
