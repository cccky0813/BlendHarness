"""Context engine: builds what the model SEES each turn from the full history, which is never modified."""

from blendharness.core.context_engine.processors.omit import omit_large_tool_args, omit_stale_code_blocks
from blendharness.core.context_engine.processors.pin import pin_at_end
from blendharness.core.context_engine.types import ContextPolicy, Messages
from blendharness.core.context_engine.utils import size_chars, tool_result_for

__all__ = ["ContextPolicy", "Messages", "omit_large_tool_args", "omit_stale_code_blocks", "pin_at_end",
           "size_chars", "tool_result_for"]
