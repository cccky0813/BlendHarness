"""Lessons: library, Reflector parsing, admission gates, and injection into the Coder (no network)."""

import json

from blendharness.agents.coder import CODER_PROMPT, make_coder
from blendharness.agents.reflector import Case, parse_lessons
from blendharness.analysis.trajectory import Issue, Trajectory
from blendharness.memory.lessons import Lesson, LessonLibrary
from blendharness.tools.blender_tools import BlenderTools
from blendharness.workflow.build import BuildConfig, run_build
from blendharness.workflow.learning.lessons import check_lesson, learn_lessons, select_cases
from tests.fakes import FakeSession, ScriptedLLM, reply
from tests.unit.test_build import CRITIC_OK, Models, coder_builds

RULE = "Derive the z of every part from the top of the bounding box of the part it rests on."


# ---------- library ----------
def test_library_round_trip_and_usage_stats(tmp_path):
    lib = LessonLibrary(tmp_path)
    assert lib.all() == [] and lib.prompt_text() == ""
    assert lib.add(Lesson("support_z", RULE, "placement", sources=["a", "b"])) is None
    assert lib.add(Lesson("support_z", "dup", "placement")) == "lesson 'support_z' already exists"
    lib.record_use(["support_z"], success=True)
    lib.record_use(["support_z"], success=False)
    lesson = lib.get("support_z")
    assert (lesson.uses, lesson.successes, lesson.sources) == (2, 1, ["a", "b"])
    assert lib.prompt_text() == f"- {RULE}"
    assert not list(tmp_path.glob("*.tmp"))                              # atomic write leaves nothing behind


# ---------- reflector parsing ----------
def test_parse_lessons_is_tolerant():
    text = 'Sure!\n```json\n{"lessons": [{"id": "a_b", "text": "x", "category": "placement", "cases": [1, "2", "z"]}]}\n```'
    lessons, error = parse_lessons(text)
    assert not error and lessons[0].cases == [1, 2]
    assert parse_lessons("no json here") == ([], True)


def test_case_rendering_shows_the_outcome():
    fixed = Case("chair", "legs float", "lower them", ["PARTIAL", "FIXED"], ["", "now on the floor"]).render(1)
    assert "fixed after 2 revision(s)" in fixed and "verifier: now on the floor" in fixed
    failed = Case("chair", "legs float", "", ["NOT_FIXED"], ["still 5 cm up"]).render(2)
    assert "NOT fixed after 1 revision(s): NOT_FIXED" in failed


# ---------- gates ----------
def test_gates():
    existing = [Lesson("support_z", RULE, "placement")]
    ok = "Place every part relative to the bounding box of its neighbour instead of absolute coordinates."
    assert check_lesson("rel_place", ok, "placement", {"t1", "t2"}, existing) is None
    assert check_lesson("Bad Id", ok, "placement", {"t1", "t2"}, existing)[0] == "static"
    assert check_lesson("short", "be careful", "placement", {"t1", "t2"}, existing)[0] == "static"
    assert check_lesson("rel_place", ok, "placement", {"t1"}, existing)[0] == "generality"
    assert check_lesson("support_z2", RULE, "placement", {"t1", "t2"}, existing)[0] == "dedup"


def trajectory(prompt, issues):
    return Trajectory("dir", "exp", "v", "task", prompt, {}, False, "not_approved", False, 2, 0.1, 1.0, issues=issues)


def test_learn_lessons_checks_citations_itself(tmp_path, tracer):
    trajectories = [
        trajectory("a chair", [Issue("backrest misaligned", 0, ["NOT_FIXED"], category="placement")]),
        trajectory("a table", [Issue("drawer off-centre", 0, ["FIXED"], category="placement")]),
        trajectory("a lamp", [Issue("shade wrong color", 0, category="material")]),
    ]
    assert [c.task for c in select_cases(trajectories, "placement")] == ["a chair", "a table"]
    proposals = {"lessons": [
        {"id": "align_to_parent", "text": "Align every part to the centre or edge of the part it attaches to.",
         "category": "placement", "cases": [1, 2]},
        {"id": "one_task_only", "text": "Always check the backrest of a chair against the seat edges.",
         "category": "placement", "cases": [1, 1]},                       # claims 2 cases, both from one task
    ]}
    llm = ScriptedLLM([reply(json.dumps(proposals))], tracer)
    lib = LessonLibrary(tmp_path)
    report = learn_lessons(llm, trajectories, ["placement", "material"], lib, log=lambda m: None)
    assert report["added"] == ["align_to_parent"]
    assert report["rejected"][0]["id"] == "one_task_only" and report["rejected"][0]["gate"] == "generality"
    assert lib.get("align_to_parent").sources == ["a chair", "a table"]
    assert "material" not in report["categories"]                        # one task only: no LLM call


# ---------- in the Coder and the pipeline ----------
def test_coder_prompt_unchanged_without_lessons(fake_session, tmp_path, tracer):
    blender = BlenderTools(fake_session, tmp_path)
    assert make_coder(ScriptedLLM([], tracer), blender).system_prompt == CODER_PROMPT
    with_lessons = make_coder(ScriptedLLM([], tracer), blender, lessons=f"- {RULE}").system_prompt
    assert with_lessons.startswith(CODER_PROMPT) and RULE in with_lessons


def test_build_uses_the_lessons_and_records_them(tmp_path):
    lib = LessonLibrary(tmp_path / "lessons")
    lib.add(Lesson("support_z", RULE, "placement"))
    models = Models(coder=coder_builds(), critic=[reply(CRITIC_OK)])
    cfg = BuildConfig(coder_model="coder", critic_model="critic", lessons_dir=str(tmp_path / "lessons"))
    record = run_build("a stool", cfg, tmp_path / "run", log=lambda m: None, llm_factory=models,
                       session_factory=lambda p: FakeSession(p))
    assert record["lessons_used"] == ["support_z"]
    assert RULE in models.llms["coder"].seen[0][0]["content"]          # in the system prompt the model saw
    assert lib.get("support_z").uses == 0                               # frozen by default (fair A/B)
