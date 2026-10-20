"""Processor: one step of the context pipeline. Takes a message list, returns a transformed COPY.

Never mutate the input: it may be the loop's full history, which must stay complete.
"""

from blendharness.core.context_engine.types import Messages


class Processor:
    @property
    def name(self) -> str:
        return type(self).__name__

    def __call__(self, messages: Messages) -> Messages:
        raise NotImplementedError
