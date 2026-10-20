"""Budget guard: the last line of defense against overflowing the model's context window.

Runs after the task-specific processors. If the view is still over budget, it shrinks the OLDEST large
contents first (tool results, tool arguments, long texts) into short placeholders until the view fits.

What it never does:
  - touch the system prompt, the task (first user message) or the most recent `keep_last` messages;
  - delete a message: tool calls and their results stay paired, so the history stays valid for the API.
If even that is not enough, the view is returned as small as it got (the API error will then say so).
"""

import copy
import json
from typing import Any

from blendharness.core.context_engine.processors.base import Processor
from blendharness.core.context_engine.token import estimate_tokens
from blendharness.core.context_engine.types import Messages

MIN_CHARS = 300  # smaller contents are not worth a placeholder


def _placeholder(n: int) -> str:
    return f"[omitted to fit the context budget: {n} chars]"


class BudgetGuard(Processor):
    def __init__(self, max_tokens: int, keep_last: int = 6) -> None:
        self.max_tokens = max_tokens
        self.keep_last = keep_last
        self.shrunk = 0                       # messages shrunk in the last call (for the trace)

    def __call__(self, messages: Messages) -> Messages:
        self.shrunk = 0
        if estimate_tokens(messages) <= self.max_tokens:
            return messages
        out = copy.deepcopy(messages)
        for i in range(2, len(out) - self.keep_last):           # oldest first; 0 = system, 1 = task
            if self._shrink(out[i]):
                self.shrunk += 1
                if estimate_tokens(out) <= self.max_tokens:
                    break
        return out

    @staticmethod
    def _shrink(m: dict[str, Any]) -> bool:
        changed = False
        content = m.get("content")
        if isinstance(content, str) and len(content) > MIN_CHARS and not content.startswith("[omitted"):
            m["content"] = _placeholder(len(content))
            changed = True
        for tc in m.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or ""
            if len(raw) <= MIN_CHARS:
                continue
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for key, value in args.items():
                if isinstance(value, str) and len(value) > MIN_CHARS:
                    args[key] = _placeholder(len(value))
                    changed = True
            tc["function"]["arguments"] = json.dumps(args, ensure_ascii=False)  # stays valid JSON
        return changed
