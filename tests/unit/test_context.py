"""Context-policy building blocks and the Coder's policy (M3 v2), pinned down before the refactor.

In P4 these become processors in a context engine; for the same input they must produce the same view.
"""

import json

from blendharness.agents.coder import executed_successfully, make_coder_context
from blendharness.core.context_engine import omit_large_tool_args, omit_stale_code_blocks, pin_at_end, size_chars, tool_result_for

BIG = "x = 1\n" * 400          # ~2400 chars, above the 1500-char threshold


def script_call(call_id: str, code: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function",
         "function": {"name": "execute_bpy", "arguments": json.dumps({"code": code})}}]}


def tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def code_of(message: dict) -> str:
    return json.loads(message["tool_calls"][0]["function"]["arguments"])["code"]


def history(*outcomes: str) -> list[dict]:
    """system + task, then one large script call per outcome ('ok' or 'err')."""
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "build a chair"}]
    for i, outcome in enumerate(outcomes, 1):
        msgs.append(script_call(f"c{i}", f"# version {i}\n" + BIG))
        msgs.append(tool_result(f"c{i}", "OK: script ran in 0.1s." if outcome == "ok" else "ERROR: boom"))
    return msgs


# ---------- building blocks ----------
def test_omit_large_tool_args_keeps_the_latest_and_never_mutates_input():
    msgs = history("ok", "ok", "ok")
    before = json.dumps(msgs)
    view = omit_large_tool_args(msgs, keep_last=1)
    assert json.dumps(msgs) == before                       # full history untouched
    assert code_of(view[2]).startswith("<omitted: version 1")
    assert code_of(view[4]).startswith("<omitted: version 2")
    assert code_of(view[6]).startswith("# version 3")


def test_omit_large_tool_args_respects_keep():
    view = omit_large_tool_args(history("err", "ok"), keep_last=0, keep=lambda m, tc: tc["id"] == "c1")
    assert code_of(view[2]).startswith("# version 1")
    assert code_of(view[4]).startswith("<omitted")


def test_small_arguments_are_never_omitted():
    msgs = [script_call("c1", "print(1)"), script_call("c2", "print(2)")]
    assert omit_large_tool_args(msgs, keep_last=0) == msgs


def test_omit_stale_code_blocks_only_touches_large_user_blocks():
    msgs = [{"role": "user", "content": f"fix this:\n```python\n{BIG}```\nand this:\n```python\nx=1\n```"},
            {"role": "assistant", "content": f"```python\n{BIG}```"}]
    view = omit_stale_code_blocks(msgs, lambda m, i: True)
    assert "<omitted" in view[0]["content"] and "x=1" in view[0]["content"]
    assert view[1] == msgs[1]


def test_tool_result_for_and_pin_at_end():
    msgs = history("err")
    assert tool_result_for(msgs, "c1") == "ERROR: boom"
    assert tool_result_for(msgs, "missing") == ""
    pinned = pin_at_end(msgs, "CURRENT")
    assert pinned[-1] == {"role": "user", "content": "CURRENT"} and len(msgs) == 4


def test_size_chars_counts_text_and_tool_arguments():
    msgs = [{"role": "user", "content": "abc"}, script_call("c1", "x")]
    assert size_chars(msgs) == 3 + len(json.dumps({"code": "x"}))


# ---------- the Coder's policy ----------
class StubBlender:
    def __init__(self, current_script: str) -> None:
        self.current_script = current_script


def test_coder_policy_pins_current_script_and_hides_all_copies():
    view = make_coder_context(StubBlender("# working\n" + BIG))(history("ok", "ok"))
    assert code_of(view[2]).startswith("<omitted") and code_of(view[4]).startswith("<omitted")
    assert view[-1]["role"] == "user" and view[-1]["content"].startswith("CURRENT WORKING SCRIPT")
    assert "# working" in view[-1]["content"]


def test_coder_policy_keeps_the_latest_failed_script_visible():
    view = make_coder_context(StubBlender("# working\n" + BIG))(history("ok", "err"))
    assert code_of(view[2]).startswith("<omitted")
    assert code_of(view[4]).startswith("# version 2")      # the model still has to fix this one


def test_coder_policy_without_a_working_script_pins_nothing():
    view = make_coder_context(StubBlender(""))(history("err"))
    assert view[-1]["role"] == "tool"
    assert code_of(view[2]).startswith("# version 1")


# ---------- the Coder's completion check ----------
def test_completion_requires_a_successful_run():
    assert executed_successfully(history("err")) is not None
    assert executed_successfully(history("err", "ok")) is None
    edited = history() + [tool_result("e1", "OK: 2 edit(s) applied, script ran in 0.1s.")]
    assert executed_successfully(edited) is None
