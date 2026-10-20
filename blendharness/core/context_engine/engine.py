"""Context engine: a pipeline of processors that turns the full history into what the model sees.

    full history --> [processor 1] --> [processor 2] --> ... --> [BudgetGuard] --> view

Each agent configures its own pipeline (e.g. the Coder: omit stale scripts, pin the current one,
guard the budget). The engine is itself a ContextPolicy, so the loop just calls it.
"""

from blendharness.core.context_engine.processors.base import Processor
from blendharness.core.context_engine.token import estimate_tokens
from blendharness.core.context_engine.types import Messages


class ContextEngine:
    def __init__(self, processors: list[Processor]) -> None:
        self.processors = processors
        self.last_report = ""        # e.g. "12034 tok | OmitLargeToolArgs -8120 | Pin +950 | = 4864 tok"

    def __call__(self, messages: Messages) -> Messages:
        view = messages
        before = estimate_tokens(view)
        steps = []
        for processor in self.processors:
            size = estimate_tokens(view)
            view = processor(view)
            delta = estimate_tokens(view) - size
            if delta:
                steps.append(f"{processor.name} {delta:+d}")
        self.last_report = " | ".join([f"{before} tok", *steps, f"= {estimate_tokens(view)} tok"])
        return view
