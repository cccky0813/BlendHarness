"""Test doubles: a scripted LLM and a fake Blender session.

Unit tests must run in seconds, for free, without network or Blender. So instead of the real model we
replay a fixed script of replies, and instead of Blender we fake just enough of its answers.
"""

import json

from blendharness.core.llm import LLMResponse, ToolCall
from blendharness.core.tracing import Tracer
from blendharness.sandbox.session import ExecResult


# ---------- scripted LLM ----------
def call(name: str, call_id: str, **arguments) -> ToolCall:
    """One tool call the fake model 'decides' to make."""
    return ToolCall(id=call_id, name=name, arguments=arguments)


def reply(text: str = "", calls: list[ToolCall] | None = None, finish: str | None = None,
          cost: float = 0.0) -> LLMResponse:
    """One fake model reply, shaped exactly like LLMClient.chat() returns it."""
    calls = calls or []
    raw = {"role": "assistant", "content": text}
    if calls:
        raw["tool_calls"] = [{"id": c.id, "type": "function",
                              "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                             for c in calls]
    finish = finish or ("tool_calls" if calls else "stop")
    return LLMResponse(text=text, tool_calls=calls, finish_reason=finish, cost_usd=cost, raw_message=raw)


class ScriptedLLM:
    """Stands in for LLMClient: returns the given replies in order and records what it was shown."""

    def __init__(self, replies: list[LLMResponse], tracer: Tracer) -> None:
        self.replies = list(replies)
        self.tracer = tracer
        self.seen: list[list[dict]] = []   # the messages (view) passed to each chat() call

    def chat(self, messages, tools=None, agent="agent") -> LLMResponse:
        self.seen.append(list(messages))   # snapshot: the loop keeps appending to the same list
        if not self.replies:
            raise AssertionError("the loop called the model more times than the test scripted")
        return self.replies.pop(0)


# ---------- fake Blender ----------
class FakeSession:
    """Fakes BlenderSession.execute(). Conventions inside test scripts:
    - a line `raise ...` makes the script fail;
    - every `make_mesh("Name")` creates one mesh object called Name.
    The Scene-Inspector is recognised by its output prefix and answered with those objects.
    """

    def __init__(self, log_path=None) -> None:
        self.executed: list[str] = []
        self.restarts = 0

    def __enter__(self) -> "FakeSession":    # used like BlenderSession: `with session_factory(log) as s:`
        return self

    def __exit__(self, *exc) -> None:
        pass

    def execute(self, code: str, reset: bool = True, timeout_s: float | None = None) -> ExecResult:
        self.executed.append(code)
        if "\nraise " in "\n" + code:
            return ExecResult(ok=False, error="RuntimeError: boom",
                              traceback='File "<agent_code>", line 1, in <module>')
        names = [line.split('"')[1] for line in code.splitlines() if line.strip().startswith('make_mesh("')]
        if "BA_SCENE_RESULT=" in code:
            objects = [{"name": n, "type": "MESH", "location": [0, 0, 0], "scale": [1, 1, 1],
                        "dimensions": [1, 1, 1], "vertex_count": 8, "material_names": [], "parent": None,
                        "bbox": {"size": [1, 1, 1]}} for n in names]
            summary = {"object_count": len(names), "mesh_object_count": len(names),
                       "total_vertex_count": 8 * len(names)}
            return ExecResult(ok=True, output="BA_SCENE_RESULT=" + json.dumps(
                {"summary": summary, "objects": objects}))
        return ExecResult(ok=True, output="", duration_s=0.01)
