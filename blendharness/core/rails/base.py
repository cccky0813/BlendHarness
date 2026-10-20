"""Rail: a pluggable rule that watches the agent loop and may steer or stop it.

The loop is the mechanism (call model, run tools, keep the history valid); rails are the policy
(when is the model finished, when is it stuck, how much may it spend). A rail overrides only the
events it cares about; the others return None ("no opinion").

Events, in the order they happen within one step:
    before_run       once per run: reset per-run state (the same loop object is run many times)
    after_llm        the model replied (with or without tool calls)
    on_final_answer  the reply has no tool calls, i.e. the model says it is done
    after_tool       one tool call has been executed
    after_step       all tool calls of this step have been executed
"""

from blendharness.core.llm import LLMResponse, ToolCall
from blendharness.core.rails.events import Decision, LoopContext
from blendharness.core.tools import ToolResult


class Rail:
    priority: int = 100          # lower runs first; the first decision at an event wins

    @property
    def name(self) -> str:
        return type(self).__name__

    def before_run(self, ctx: LoopContext) -> None:
        pass

    def after_llm(self, ctx: LoopContext, resp: LLMResponse) -> Decision:
        return None

    def on_final_answer(self, ctx: LoopContext, resp: LLMResponse) -> Decision:
        return None

    def after_tool(self, ctx: LoopContext, call: ToolCall, result: ToolResult) -> Decision:
        return None

    def after_step(self, ctx: LoopContext) -> Decision:
        return None
