"""The whole build pipeline (Coder -> Critic -> revise -> Verifier) on fakes: no network, no Blender.

Written BEFORE the pipeline was split into checkpointed phases (P5), to pin down its behaviour.
"""

import json

import pytest

from blendharness.core.session import Checkpointer
from blendharness.workflow.build import BuildConfig, run_build
from tests.fakes import FakeSession, ScriptedLLM, call, reply

SEAT = 'make_mesh("Seat")\nmake_mesh("Leg")\n'
CRITIC_ISSUE = json.dumps({"approved": False, "issues": [{"problem": "no backrest", "fix": "add a backrest"}]})
CRITIC_OK = json.dumps({"approved": True, "issues": []})
VERIFIED = json.dumps({"issues": [{"index": 1, "verdict": "FIXED", "evidence": "backrest added"}], "regressions": []})


class Models:
    """llm_factory for run_build: one scripted LLM per model name (all sharing the run's tracer)."""

    def __init__(self, **replies) -> None:
        self.replies = replies                 # model name -> list of replies
        self.llms: dict[str, ScriptedLLM] = {}

    def __call__(self, model, tracer, extra_body=None):
        if model not in self.llms:
            self.llms[model] = ScriptedLLM(self.replies.get(model, []), tracer)
        self.llms[model].tracer = tracer
        return self.llms[model]


def coder_builds(code=SEAT):
    return [reply(calls=[call("execute_bpy", "b1", code=code)]), reply("built it")]


def coder_adds_backrest():
    return [reply(calls=[call("edit_script", "e1", edits=[{"old_string": 'make_mesh("Leg")\n',
                                                          "new_string": 'make_mesh("Leg")\nmake_mesh("Back")\n'}])]),
            reply("added a backrest")]


CFG = BuildConfig(coder_model="coder", critic_model="critic", rounds=2)


@pytest.fixture
def sessions():
    created = []
    def factory(log_path):
        created.append(FakeSession(log_path))
        return created[-1]
    factory.created = created
    return factory


def test_approved_in_round_zero(tmp_path, sessions):
    models = Models(coder=coder_builds(), critic=[reply(CRITIC_OK)])
    record = run_build("a stool", CFG, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions)
    assert (record["approved"], record["status"], record["n_rounds"]) == (True, "approved", 1)
    assert (tmp_path / "run" / "final_script.py").read_text() == SEAT
    assert json.loads((tmp_path / "run" / "result.json").read_text())["approved"] is True


def test_issue_fixed_in_round_one(tmp_path, sessions):
    models = Models(coder=coder_builds() + coder_adds_backrest(),
                    critic=[reply(CRITIC_ISSUE), reply(VERIFIED)])    # critic model also verifies
    record = run_build("a chair", CFG, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions)
    assert record["approved"] and record["n_rounds"] == 2
    r0, r1 = record["rounds"]
    assert r0["critic_approved"] is False and r0["issues"][0]["problem"] == "no backrest"
    assert r1["verified"] is True and r1["diff"]["added"] == ["Back"]
    assert record["open_issues"] == [] and record["executions"] == 2
    assert 'make_mesh("Back")' in (tmp_path / "run" / "final_script.py").read_text()


def test_unchanged_scene_fails_verification_without_an_llm_call(tmp_path, sessions):
    models = Models(coder=coder_builds() + [reply("I think it is fine now")] * 2 + [reply("still fine")] * 2,
                    critic=[reply(CRITIC_ISSUE)])
    cfg = BuildConfig(coder_model="coder", critic_model="critic", rounds=1)
    record = run_build("a chair", cfg, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions)
    assert record["status"] == "not_approved"
    assert record["rounds"][1]["verifier_skipped_llm"] is True
    assert record["open_issues"][0]["problem"] == "no backrest"


def test_no_working_script(tmp_path, sessions):
    broken = [reply(calls=[call("execute_bpy", f"b{i}", code="raise ValueError()\n")]) for i in range(3)]
    models = Models(coder=broken)
    record = run_build("a chair", CFG, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions)
    assert record["status"] == "no_script" and record["rounds"][0]["coder_status"] == "stuck"


# ---------- checkpoints: resume and fork (P5) ----------
def test_every_phase_saves_a_checkpoint(tmp_path, sessions):
    models = Models(coder=coder_builds() + coder_adds_backrest(), critic=[reply(CRITIC_ISSUE), reply(VERIFIED)])
    record = run_build("a chair", CFG, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions)
    assert record["checkpoints"] == ["round0", "round1"]
    saved = Checkpointer(tmp_path / "run").load("round0")
    assert saved["current_script"] == SEAT and saved["open_issues"][0]["problem"] == "no backrest"


def test_resume_after_a_crash_continues_without_redoing_round0(tmp_path, sessions):
    run = tmp_path / "run"
    # First attempt: round 0 completes, then the coder "crashes" in round 1 (the API goes away).
    crashing = Models(coder=coder_builds(), critic=[reply(CRITIC_ISSUE)])
    with pytest.raises(AssertionError, match="more times than the test scripted"):
        run_build("a chair", CFG, run, log=lambda m: None, llm_factory=crashing, session_factory=sessions)
    assert Checkpointer(run).labels() == ["round0"]

    # Second attempt: only round 1 is scripted. Round 0 must not run again.
    resumed = Models(coder=coder_adds_backrest(), critic=[reply(VERIFIED)])
    record = run_build("ignored: the saved prompt wins", BuildConfig(), run, log=lambda m: None,
                       llm_factory=resumed, session_factory=sessions, resume=True)
    assert record["approved"] and record["prompt"] == "a chair" and record["config"]["coder_model"] == "coder"
    assert [r["round"] for r in record["rounds"]] == [0, 1]
    assert record["executions"] == 2                                    # history restored from the checkpoint
    assert resumed.llms["coder"].replies == []                          # consumed exactly round 1's replies
    runs = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()
            if '"kind": "run"' in line]
    assert [r["status"] for r in runs] == ["error", "ok"]               # both attempts are in one trace
    assert record["checkpoints"] == ["round0", "round1"]


def test_resume_without_checkpoint_starts_fresh(tmp_path, sessions):
    models = Models(coder=coder_builds(), critic=[reply(CRITIC_OK)])
    record = run_build("a stool", CFG, tmp_path / "run", log=lambda m: None,
                       llm_factory=models, session_factory=sessions, resume=True)
    assert record["approved"] and record["checkpoints"] == ["round0"]


def test_fork_reuses_round0_with_a_different_config(tmp_path, sessions):
    # Parent: build + critique only (no revision rounds allowed).
    parent = Models(coder=coder_builds(), critic=[reply(CRITIC_ISSUE)])
    no_revisions = BuildConfig(coder_model="coder", critic_model="critic", rounds=0)
    first = run_build("a chair", no_revisions, tmp_path / "parent", log=lambda m: None,
                      llm_factory=parent, session_factory=sessions)
    assert first["status"] == "not_approved" and first["n_rounds"] == 1

    # Fork: same round-0 result, now with one revision round and another coder model.
    child = Models(**{"other-coder": coder_adds_backrest(), "critic": [reply(VERIFIED)]})
    cfg = BuildConfig(coder_model="other-coder", critic_model="critic", rounds=1)
    record = run_build("ignored", cfg, tmp_path / "child", log=lambda m: None,
                       llm_factory=child, session_factory=sessions, fork_from=(tmp_path / "parent", "round0"))
    assert record["approved"] and record["forked_from"].endswith("parent@round0")
    assert record["config"]["coder_model"] == "other-coder"
    assert record["checkpoints"] == ["fork", "round1"]
    assert Checkpointer(tmp_path / "parent").labels() == ["round0"]    # the parent is untouched


def test_fork_from_a_missing_checkpoint_says_what_exists(tmp_path, sessions):
    models = Models(coder=coder_builds(), critic=[reply(CRITIC_OK)])
    run_build("a stool", CFG, tmp_path / "parent", log=lambda m: None, llm_factory=models, session_factory=sessions)
    with pytest.raises(ValueError, match=r"no checkpoint 'round3'.*\['round0'\]"):
        run_build("x", CFG, tmp_path / "child", llm_factory=models, session_factory=sessions,
                  fork_from=(tmp_path / "parent", "round3"))
