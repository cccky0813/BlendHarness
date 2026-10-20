"""The rails mechanism itself: custom rails, priority, per-run state, trace annotations.

(The behaviour of the four standard rails is covered by test_loop.py, unchanged since before rails existed.)
"""

from blendharness.core.rails import Rail, StuckRail, TruncationRail
from blendharness.core.rails.events import Nudge, Stop
from blendharness.core.runtime import AgentLoop
from blendharness.core.tools import Tool, ToolResult
from tests.fakes import call, reply


def echo_tool() -> Tool:
    return Tool("echo", "Echo.", {"type": "object", "properties": {"text": {"type": "string"}}},
                lambda text="": f"echo: {text}")


def failing_tool() -> Tool:
    return Tool("fail", "Fails.", {"type": "object", "properties": {}},
                lambda: ToolResult("ERROR: same failure", is_error=True))


class ForbidTool(Rail):
    """Example policy rail: stop as soon as a forbidden tool has been called."""
    def __init__(self, name: str) -> None:
        self.forbidden = name

    def after_tool(self, ctx, call, result):
        return Stop("forbidden", f"{call.name} is not allowed") if call.name == self.forbidden else None


class AlwaysSummarize(Rail):
    """Example steering rail: before accepting the first final answer, ask for a summary."""
    def before_run(self, ctx):
        self.asked = False

    def on_final_answer(self, ctx, resp):
        if self.asked:
            return None
        self.asked = True
        return Nudge("Summarize what you did in one sentence.")


def test_custom_rail_can_stop_and_remaining_calls_are_skipped(scripted):
    llm = scripted([reply(calls=[call("echo", "c1", text="a"), call("echo", "c2", text="b")])])
    r = AgentLoop("t", llm, "sys", [echo_tool()], rails=[ForbidTool("echo")]).run("task")
    assert (r.status, r.reason) == ("forbidden", "echo is not allowed")
    assert [m["content"][:7] for m in r.messages if m["role"] == "tool"] == ["echo: a", "SKIPPED"]


def test_custom_rail_can_nudge(scripted):
    llm = scripted([reply("done"), reply("I echoed nothing.")])
    r = AgentLoop("t", llm, "sys", [echo_tool()], rails=[AlwaysSummarize()]).run("task")
    assert r.status == "done" and r.final_text == "I echoed nothing."
    assert llm.seen[1][-1]["content"] == "Summarize what you did in one sentence."


def test_lower_priority_number_decides_first(scripted):
    class Early(Rail):
        priority = 1
        def on_final_answer(self, ctx, resp): return Stop("early", "")
    class Late(Rail):
        priority = 99
        def on_final_answer(self, ctx, resp): return Stop("late", "")
    llm = scripted([reply("done")])
    assert AgentLoop("t", llm, "sys", [], rails=[Late(), Early()]).run("task").status == "early"


def test_standard_rails_run_before_extra_rails_with_default_priority(scripted):
    loop = AgentLoop("t", scripted([]), "sys", [], max_cost_usd=1.0, completion_check=lambda m: None,
                     rails=[ForbidTool("x")])
    assert [r.name for r in loop.rails] == ["TruncationRail", "StuckRail", "CostBudgetRail",
                                            "CompletionRail", "ForbidTool"]


def test_rail_state_is_reset_for_every_run(scripted):
    # The same loop object is run once per revision round; counters must not leak between runs.
    two_errors_then_done = [reply(calls=[call("fail", "a")]), reply(calls=[call("fail", "b")]), reply("ok")]
    llm = scripted(two_errors_then_done * 2)
    loop = AgentLoop("t", llm, "sys", [failing_tool()], max_same_error=3)
    assert loop.run("first").status == "done"
    assert loop.run("second").status == "done"     # would be "stuck" if the count carried over


def test_nudge_with_pending_tool_calls_answers_them_first(scripted):
    class NudgeAfterLLM(Rail):
        def before_run(self, ctx): self.done = False
        def after_llm(self, ctx, resp):
            if not self.done:
                self.done = True
                return Nudge("slow down")
            return None
    llm = scripted([reply(calls=[call("echo", "c1", text="x")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [echo_tool()], rails=[NudgeAfterLLM()]).run("task")
    roles = [m["role"] for m in r.messages]
    assert roles == ["system", "user", "assistant", "tool", "user", "assistant"]
    assert r.messages[3]["content"].startswith("SKIPPED")       # the history stays valid


def test_decisions_are_recorded_in_the_trace(scripted, tracer):
    llm = scripted([reply("a", finish="length"), reply("b")])
    AgentLoop("coder", llm, "sys", []).run("task")
    agent_span = tracer.spans[-1]
    assert agent_span.attrs["rail_step1"].startswith("TruncationRail.after_llm -> Nudge(")


def test_rails_are_plain_objects_usable_on_their_own():
    rail = StuckRail(max_same_error=2)
    rail.before_run(None)
    err = ToolResult("ERROR: x", is_error=True)
    assert rail.after_tool(None, call("t", "1"), err) is None
    assert rail.after_tool(None, call("t", "2"), err) == Stop("stuck", "same error 2x: t: ERROR: x")
    assert TruncationRail().after_llm(None, reply("ok")) is None
