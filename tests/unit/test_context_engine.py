"""Context engine: token estimate, processors as pipeline steps, budget guard, report."""

import json

from blendharness.agents.coder import make_coder_context
from blendharness.core.context_engine import (BudgetGuard, ContextEngine, OmitLargeToolArgs, Pin, Processor,
                                              estimate_tokens)
from blendharness.core.runtime import AgentLoop
from blendharness.core.tools import Tool
from tests.fakes import call, reply


def text(role, n):
    return {"role": role, "content": "x" * n}


def tool_pair(call_id, arg_chars, result_chars):
    return [{"role": "assistant", "content": "", "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": "execute_bpy", "arguments": json.dumps({"code": "y" * arg_chars})}}]},
            {"role": "tool", "tool_call_id": call_id, "content": "r" * result_chars}]


# ---------- token estimate ----------
def test_estimate_counts_text_arguments_and_images():
    assert estimate_tokens([text("user", 2300)]) == 1000                      # 2.3 chars per token
    image = {"role": "user", "content": [{"type": "text", "text": ""},
                                         {"type": "image_url", "image_url": {"url": "data:..."}}]}
    assert estimate_tokens([image]) == 350
    assert estimate_tokens(tool_pair("c", 2300 - 12, 0)) == 1000             # '{"code": ""}' is 12 chars


# ---------- pipeline ----------
class Upper(Processor):
    def __call__(self, messages):
        return [{**m, "content": m["content"].upper()} for m in messages]


def test_engine_runs_processors_in_order_and_reports():
    engine = ContextEngine([Pin(lambda: "y" * 230), Upper()])
    view = engine([text("system", 230)])
    assert [m["content"] for m in view] == ["X" * 230, "Y" * 230]
    assert engine.last_report == "100 tok | Pin +100 | = 200 tok"           # Upper changed nothing in size


def test_pin_without_content_adds_nothing():
    msgs = [text("user", 10)]
    assert Pin(lambda: None)(msgs) is msgs


def test_processors_never_mutate_the_history():
    msgs = [text("system", 10), text("user", 10)] + tool_pair("a", 3000, 10) + tool_pair("b", 3000, 10)
    before = json.dumps(msgs)
    ContextEngine([OmitLargeToolArgs(keep_last=1), Pin(lambda: "p"), BudgetGuard(10)])(msgs)
    assert json.dumps(msgs) == before


# ---------- budget guard ----------
def test_budget_guard_does_nothing_under_budget():
    msgs = [text("system", 100), text("user", 100)]
    assert BudgetGuard(1000)(msgs) is msgs


def test_budget_guard_shrinks_oldest_first_and_keeps_pairs():
    msgs = [text("system", 5000), text("user", 5000)] + tool_pair("a", 4000, 4000) + tool_pair("b", 4000, 4000) \
        + [text("assistant", 4000)]
    guard = BudgetGuard(max_tokens=10000, keep_last=1)
    view = guard(msgs)
    assert estimate_tokens(view) <= 10000
    assert view[0] == msgs[0] and view[1] == msgs[1]                       # system + task untouched
    assert view[-1] == msgs[-1]                                            # recent messages untouched
    assert "omitted to fit the context budget" in json.loads(view[2]["tool_calls"][0]["function"]["arguments"])["code"]
    assert view[3]["content"].startswith("[omitted to fit the context budget")
    assert view[5] == msgs[5]                                              # stopped as soon as it fit
    assert len(view) == len(msgs) and [m["role"] for m in view] == [m["role"] for m in msgs]
    assert guard.shrunk == 2


def test_budget_guard_gives_up_gracefully_when_protected_parts_are_too_big():
    msgs = [text("system", 50000), text("user", 50000)]
    assert BudgetGuard(max_tokens=100)(msgs) == msgs


# ---------- in the agent ----------
class StubBlender:
    current_script = "make_mesh('Seat')\n"


def test_coder_engine_report_lands_in_the_trace(scripted, tracer):
    run = Tool("execute_bpy", "Run.", {"type": "object", "properties": {}}, lambda code: "OK: script ran")
    llm = scripted([reply(calls=[call("execute_bpy", "c1", code="z" * 3000)]), reply("ok")])
    loop = AgentLoop("coder", llm, "sys", [run], context_policy=make_coder_context(StubBlender()))
    loop.run("task")
    attrs = tracer.spans[-1].attrs
    assert attrs["context_step1"].endswith("tok") and "Pin +" in attrs["context_step1"]
    assert "OmitLargeToolArgs -" in attrs["context_step2"]                 # it ran OK, so its copy is omitted
