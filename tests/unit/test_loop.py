"""Behaviour of AgentLoop, pinned down before the refactor.

Each test replays a fixed sequence of model replies and checks what the harness does with them.
After the loop is split into runtime + rails (P2) these tests must still pass unchanged.
"""

from harness.loop import AgentLoop, Tool, ToolResult
from tests.fakes import call, reply


def echo_tool() -> Tool:
    return Tool("echo", "Echo the text back.", {"type": "object", "properties": {"text": {"type": "string"}}},
                lambda text: f"echo: {text}")


def failing_tool(message: str = "ERROR: same failure") -> Tool:
    return Tool("fail", "Always fails.", {"type": "object", "properties": {}},
                lambda: ToolResult(message, is_error=True))


def tool_messages(messages):
    return [m for m in messages if m["role"] == "tool"]


# ---------- normal flow ----------
def test_answer_without_tools_is_done(scripted):
    llm = scripted([reply("all good")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert (r.status, r.final_text, r.steps) == ("done", "all good", 1)
    assert [m["role"] for m in r.messages] == ["system", "user", "assistant"]


def test_tool_result_is_paired_with_its_call_id(scripted):
    llm = scripted([reply(calls=[call("echo", "c1", text="hi"), call("echo", "c2", text="yo")]),
                    reply("finished")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert r.status == "done" and r.steps == 2
    assert [(m["tool_call_id"], m["content"]) for m in tool_messages(r.messages)] == [
        ("c1", "echo: hi"), ("c2", "echo: yo")]
    # the second model call saw the tool results
    assert llm.seen[1][-1]["content"] == "echo: yo"


def test_cost_is_summed_over_calls(scripted):
    llm = scripted([reply(calls=[call("echo", "c1", text="a")], cost=0.1), reply("ok", cost=0.05)])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert abs(r.cost_usd - 0.15) < 1e-9


# ---------- errors become observations ----------
def test_unknown_tool_is_reported_not_raised(scripted):
    llm = scripted([reply(calls=[call("nope", "c1")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert r.status == "done"
    assert tool_messages(r.messages)[0]["content"].startswith("ERROR: unknown tool 'nope'")


def test_invalid_json_arguments_are_reported(scripted):
    llm = scripted([reply(calls=[call("echo", "c1", __invalid_json__="{bad")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert tool_messages(r.messages)[0]["content"].startswith("ERROR: arguments were not valid JSON")


def test_crashing_tool_becomes_error_message(scripted):
    def boom(): raise ValueError("bad input")
    crash = Tool("crash", "Crashes.", {"type": "object", "properties": {}}, boom)
    llm = scripted([reply(calls=[call("crash", "c1")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [crash]).run("task")
    assert r.status == "done"
    assert tool_messages(r.messages)[0]["content"] == "ERROR: ValueError: bad input"


def test_huge_tool_output_is_truncated(scripted):
    big = Tool("big", "Big output.", {"type": "object", "properties": {}}, lambda: "x" * 10000)
    llm = scripted([reply(calls=[call("big", "c1")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [big]).run("task")
    content = tool_messages(r.messages)[0]["content"]
    assert content.startswith("x" * 6000) and "[truncated: 4000 more chars not shown]" in content


# ---------- budgets ----------
def test_same_error_three_times_is_stuck_and_skips_the_rest(scripted):
    llm = scripted([reply(calls=[call("fail", "a1")]),
                    reply(calls=[call("fail", "a2")]),
                    reply(calls=[call("fail", "a3"), call("echo", "a4", text="never runs")])])
    r = AgentLoop("t", llm, "sys", [failing_tool(), echo_tool()], max_same_error=3).run("task")
    assert r.status == "stuck" and r.steps == 3
    assert "same error 3x" in r.reason
    # every tool call still gets an answer, otherwise the history is invalid for the API
    last = tool_messages(r.messages)[-1]
    assert last["tool_call_id"] == "a4" and last["content"].startswith("SKIPPED")


def test_different_errors_do_not_count_as_stuck(scripted):
    counter = iter(range(10))
    varying = Tool("fail", "Fails differently.", {"type": "object", "properties": {}},
                   lambda: ToolResult(f"ERROR: failure {next(counter)}", is_error=True))
    llm = scripted([reply(calls=[call("fail", f"c{i}")]) for i in range(3)] + [reply("gave up")])
    r = AgentLoop("t", llm, "sys", [varying], max_same_error=3).run("task")
    assert r.status == "done"


def test_max_steps(scripted):
    llm = scripted([reply(calls=[call("echo", f"c{i}", text="again")]) for i in range(3)])
    r = AgentLoop("t", llm, "sys", [echo_tool()], max_steps=3).run("task")
    assert (r.status, r.steps, r.final_text) == ("max_steps", 3, "")


def test_cost_budget(scripted):
    llm = scripted([reply(calls=[call("echo", "c1", text="a")], cost=0.3),
                    reply(calls=[call("echo", "c2", text="b")], cost=0.3)])
    r = AgentLoop("t", llm, "sys", [echo_tool()], max_cost_usd=0.5).run("task")
    assert r.status == "over_budget" and r.steps == 2
    # the budget is checked after the tools ran, so the call is still answered
    assert tool_messages(r.messages)[-1]["tool_call_id"] == "c2"


# ---------- truncated output ----------
def test_one_truncation_is_nudged_and_recovers(scripted):
    llm = scripted([reply("thinking...", finish="length"), reply("done now")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert r.status == "done" and r.steps == 2
    assert "cut off by the output length limit" in llm.seen[1][-1]["content"]


def test_two_truncations_in_a_row_stop(scripted):
    llm = scripted([reply("a", finish="length"), reply("b", finish="length")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert r.status == "truncated" and r.steps == 2


def test_truncation_counter_resets_after_a_normal_reply(scripted):
    llm = scripted([reply("a", finish="length"), reply(calls=[call("echo", "c1", text="x")]),
                    reply("b", finish="length"), reply("ok")])
    r = AgentLoop("t", llm, "sys", [echo_tool()]).run("task")
    assert r.status == "done"


# ---------- completion check ----------
def test_completion_check_rejects_then_accepts(scripted):
    def check(messages):
        ran = any(m["role"] == "tool" for m in messages)
        return None if ran else "you did not run anything"
    llm = scripted([reply("I'm done"), reply(calls=[call("echo", "c1", text="x")]), reply("now done")])
    r = AgentLoop("t", llm, "sys", [echo_tool()], completion_check=check).run("task")
    assert r.status == "done" and r.steps == 3
    assert llm.seen[1][-1]["content"] == "Not finished yet: you did not run anything"


def test_completion_check_gives_up_after_two_nudges(scripted):
    llm = scripted([reply("done"), reply("done!"), reply("really done")])
    r = AgentLoop("t", llm, "sys", [echo_tool()], completion_check=lambda m: "still missing").run("task")
    assert (r.status, r.reason, r.steps) == ("unverified", "still missing", 3)


# ---------- context policy ----------
def test_model_sees_the_view_but_history_stays_complete(scripted):
    def last_two(messages):
        return messages[:1] + messages[-1:]          # a deliberately aggressive compaction
    llm = scripted([reply(calls=[call("echo", "c1", text="x")]), reply("ok")])
    r = AgentLoop("t", llm, "sys", [echo_tool()], context_policy=last_two).run("task")
    assert len(llm.seen[1]) == 2                     # the model saw the compacted view
    assert len(r.messages) == 5                      # system, user, assistant, tool, assistant


# ---------- tracing ----------
def test_agent_and_tool_spans_are_traced(scripted, tracer):
    llm = scripted([reply(calls=[call("echo", "c1", text="x")]), reply("ok")])
    AgentLoop("coder", llm, "sys", [echo_tool()]).run("task")
    kinds = [(s.kind, s.name) for s in tracer.spans]
    assert kinds == [("tool", "echo"), ("agent", "coder")]   # children close (and are written) first
    tool_span, agent_span = tracer.spans
    assert tool_span.parent_id == agent_span.span_id
    assert agent_span.attrs["status"] == "done"
