"""Context engine: builds what the model SEES each turn from the full history, which is never modified.

    ContextEngine([processors...])   a pipeline; itself a ContextPolicy (messages -> view)
    processors/                      OmitLargeToolArgs, OmitStaleCodeBlocks, Pin, BudgetGuard
    token.py                         token estimate calibrated on our own traces
"""

from blendharness.core.context_engine.engine import ContextEngine
from blendharness.core.context_engine.processors.base import Processor
from blendharness.core.context_engine.processors.budget_guard import BudgetGuard
from blendharness.core.context_engine.processors.omit import (OmitLargeToolArgs, OmitStaleCodeBlocks,
                                                              omit_large_tool_args, omit_stale_code_blocks)
from blendharness.core.context_engine.processors.pin import Pin, pin_at_end
from blendharness.core.context_engine.token import estimate_tokens
from blendharness.core.context_engine.types import ContextPolicy, Messages
from blendharness.core.context_engine.utils import size_chars, tool_result_for

__all__ = ["BudgetGuard", "ContextEngine", "ContextPolicy", "Messages", "OmitLargeToolArgs", "OmitStaleCodeBlocks",
           "Pin", "Processor", "estimate_tokens", "omit_large_tool_args", "omit_stale_code_blocks", "pin_at_end",
           "size_chars", "tool_result_for"]
