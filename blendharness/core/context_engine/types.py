"""Context management: what the model SEES each turn, without losing the full history.

The loop keeps the complete message history (for logs, debugging and training data).
A context policy builds a compacted COPY of it right before every model call.

Trade-off: rewriting an earlier message changes the prompt prefix, so provider-side prompt
caching cannot reuse that prefix any more. Compacting each block once (when it becomes stale)
keeps the cache misses rare.
"""

from typing import Any, Callable

Messages = list[dict[str, Any]]
ContextPolicy = Callable[[Messages], Messages]  # full history -> what the model sees
