"""Data-flow walkthrough: ONE build that exercises every mechanism, on a scripted fake LLM and a fake Blender
(free: no network, no Blender). Read the output top to bottom next to the code.

    python scripts/walkthrough.py

What the scripted build goes through (tags in the output):
    [MEMORY-READ]   lessons -> Coder's system prompt; skill index -> prompt; load_skill -> preamble
    [TOOLS]         the tool registry built for the Coder, every tool call with its permission
    [GUARD]         the sandbox guard blocks `import os` before anything reaches Blender
    [RAIL]          CompletionRail rejects an early "I'm done"
    [CONTEXT]       the context engine omits an old large script and pins the current one
    [CHECKPOINT]    BuildState saved after every phase
    [MEMORY-WRITE]  the Librarian distils a skill; four admission gates; written to the skill library
    [TRACE]         the span tree of the whole build
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))      # this teaching script reuses the test doubles in tests/fakes.py

from blendharness.core.llm import ToolCall  # noqa: E402
from blendharness.core.tools.registry import ToolRegistry  # noqa: E402
from blendharness.memory.lessons import Lesson, LessonLibrary  # noqa: E402
from blendharness.memory.skills import Skill, SkillLibrary  # noqa: E402
from blendharness.workflow.build import BuildConfig, run_build  # noqa: E402
from tests.fakes import FakeSession, ScriptedLLM, call, reply  # noqa: E402


def banner(tag: str, text: str) -> None:
    print(f"\n{'═' * 100}\n[{tag}] {text}\n{'═' * 100}")


def short(x, n=160) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + f"... ({len(s)} chars)"


# ---------- instrumentation: print every model call and every tool call as it happens ----------
class LoggingLLM(ScriptedLLM):
    def chat(self, messages, tools=None, agent="agent"):
        print(f"\n  ▶ LLM call by [{agent}]: the model is SHOWN {len(messages)} messages:")
        for m in messages:
            extra = f" tool_calls={[tc['function']['name'] for tc in m['tool_calls']]}" if m.get("tool_calls") else ""
            idref = f" (answers {m['tool_call_id']})" if m.get("tool_call_id") else ""
            print(f"      {m['role']:<9}{idref}{extra}: {short(m.get('content') or '', 110)}")
        resp = super().chat(messages, tools, agent)
        what = [f"{c.name}({short(c.arguments, 70)}) id={c.id}" for c in resp.tool_calls] or [f"text: {short(resp.text, 90)}"]
        print(f"  ◀ the model ANSWERS: {what}")
        return resp


_execute = ToolRegistry.execute


def logging_execute(self, call, tracer):
    tool = self.get(call.name)
    print(f"    [TOOLS] registry.execute({call.name}, id={call.id}) permission={tool.permission if tool else '?'}")
    result = _execute(self, call, tracer)
    tag = "GUARD" if "sandbox policy" in result.content else "TOOLS"
    print(f"    [{tag}] -> ToolResult(is_error={result.is_error}): {short(result.content, 120)}")
    return result


ToolRegistry.execute = logging_execute

# ---------- long-term memory BEFORE the build ----------
work = Path(tempfile.mkdtemp())
skills = SkillLibrary(work / "skills")
skills.add(Skill("chair_leg", "One straight chair leg.",
                 'def build_chair_leg(x=0.0, y=0.0):\n    """One chair leg standing on the floor."""\n    make_mesh("Leg")\n'))
lessons = LessonLibrary(work / "lessons")
lessons.add(Lesson("derive_from_bbox", "Derive every part's position from the bounding box of the part it rests on.",
                   "placement", sources=["a", "b"]))

# ---------- what the scripted models will say ----------
LONG_SCRIPT = 'build_chair_leg()\nmake_mesh("Seat")\n' + "".join(f"# detail line {i:03d} of the chair script\n" for i in range(60))
script = {
    "coder": [
        # round 0
        reply(calls=[call("execute_bpy", "call_1", code="import os\nos.listdir('/')\n")]),     # -> GUARD blocks
        reply("Done, the chair is built."),                                                     # -> RAIL rejects
        reply(calls=[ToolCall("call_2", "load_skill", {"name": "chair_leg"})]),                        # -> MEMORY-READ
        reply(calls=[call("execute_bpy", "call_3", code=LONG_SCRIPT)]),                         # large script
        reply("Built a seat with a leg from the skill library."),                               # -> CONTEXT omits
        # round 1
        reply(calls=[call("edit_script", "call_4", edits=[{"old_string": 'make_mesh("Seat")\n',
                                                            "new_string": 'make_mesh("Seat")\nmake_mesh("Back")\n'}])]),
        reply("Added a backrest."),
        # learning: the librarian uses the coder model by default
        reply(json.dumps({"skills": [{"name": "backrest_panel", "description": "A flat backrest panel for a chair.",
                                      "code": 'def build_backrest_panel():\n    """A backrest panel."""\n    make_mesh("Back")\n',
                                      "tags": ["chair"], "usage": "build_backrest_panel()"}]})),
    ],
    "critic": [   # critic, verifier and skill review share this model
        reply(json.dumps({"approved": False, "issues": [{"problem": "There is no backrest.", "fix": "Add a backrest."}]})),
        reply(json.dumps({"issues": [{"index": 1, "verdict": "FIXED", "evidence": "Back was added"}], "regressions": []})),
        reply(json.dumps({"approved": True, "issues": []})),
    ],
}
llms = {}


def llm_factory(model, tracer, extra_body=None):
    if model not in llms:
        llms[model] = LoggingLLM(script[model], tracer)
    return llms[model]


def log(message: str) -> None:
    banner("WORKFLOW", message.strip("- "))


# ---------- run ----------
prompt = "Create a chair with a backrest."
cfg = BuildConfig(coder_model="coder", critic_model="critic", rounds=2, skills_dir=str(work / "skills"),
                  learn_skills=True, lessons_dir=str(work / "lessons"))
banner("INPUT", f"run_build({prompt!r}, BuildConfig(rounds=2, skills_dir=..., learn_skills=True, lessons_dir=...))")
print(f"  long-term memory before: skills={skills.names()}, lessons={[x.id for x in lessons.all()]}")

run_dir = work / "run"
record = run_build(prompt, cfg, run_dir, log=log, llm_factory=llm_factory,
                   session_factory=lambda p: FakeSession(p))

# ---------- afterwards: what the loop recorded, what was saved ----------
spans = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
banner("CONTEXT + RAIL", "what the agent loop recorded per step (attributes of each 'agent coder' span)")
for i, s in enumerate(x for x in spans if x["kind"] == "agent" and x["name"] == "coder"):
    print(f"  coder run {i} (round {i}): status={s['attrs']['status']} steps={s['attrs']['steps']}")
    for k, v in s["attrs"].items():
        if k.startswith(("context_step", "rail_step", "completion_rejected")):
            print(f"      {k:<22} {v}")

banner("TOOLS", "the Coder's system prompt ends with the lessons and the skill index (MEMORY-READ at build start)")
system = llms["coder"].seen[0][0]["content"]
print("  ..." + system[system.index("Lessons learned"):])

banner("CHECKPOINT", "BuildState after each phase (checkpoints/*.json), selected fields")
for path in sorted((run_dir / "checkpoints").glob("*.json")):
    st = json.loads(path.read_text())["state"]
    print(f"  {path.name:<18} completed_rounds={st['completed_rounds']} rounds_finished={st['rounds_finished']} "
          f"approved={st['approved']} open_issues={len(st['open_issues'])} loaded_skills={st['loaded_skills']} "
          f"lesson_ids={st['lesson_ids']} learned={'yes' if st['learned'] else None}")

banner("MEMORY-WRITE", "skill learning after the approved build")
print(f"  {short(record['skills_learned'], 300)}")
print(f"  long-term memory after: skills={SkillLibrary(work / 'skills').names()}")
print(f"  usage statistics updated for the loaded skill: {json.loads((work / 'skills' / 'chair_leg' / 'stats.json').read_text())}")

banner("TRACE", "span tree (trace.jsonl)")
by_id = {s["span_id"]: s for s in spans}


def depth(s):
    d = 0
    while s.get("parent_id") in by_id:
        s, d = by_id[s["parent_id"]], d + 1
    return d


for s in sorted(spans, key=lambda s: s["start_time"]):
    print(f"  {'    ' * depth(s)}{s['kind']:<5} {s['name']}")
print(f"\nresult.json: status={record['status']}, rounds={record['n_rounds']}, skills_loaded={record['skills_loaded']}, "
      f"lessons_used={record['lessons_used']}\nrun directory: {run_dir}")
