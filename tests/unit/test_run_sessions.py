"""eval/run_sessions.py on fakes: turn 1 runs once and every arm forks from it, the system never sees the expected
changes, turns are scored with the programmatic checks, retention is checked on the last scene, reruns are free."""

import json

from eval.run_sessions import aggregate, run_experiment, score_experiment
from blendharness.workflow.build import BuildConfig
from tests.fakes import call, reply
from tests.unit.test_build import CRITIC_OK, Models, coder_builds
from tests.unit.test_session import ADD_BACK, T1, T2, sessions  # noqa: F401 (fixture)
from tests.unit.test_session_memory import ledger

SESSION = {"id": "stool", "turns": [
    {"request": T1, "expect": [{"id": "s1_seat", "exists": "seat"}, {"id": "s1_leg", "exists": "leg"}],
     "persist": ["s1_seat"]},
    {"request": T2, "expect": [{"id": "s2_back", "exists": "back"}], "touches": ["back"], "requires": ["s1_seat"],
     "rubric": [{"id": "Q1", "q": "Is there a backrest? SECRET_RUBRIC"}]},
]}
BUILD = BuildConfig(coder_model="coder", critic_model="critic", rounds=1)


def models():
    return Models(coder=coder_builds() + ADD_BACK + ADD_BACK, critic=[reply(CRITIC_OK)] * 3,
                  ledger=[ledger(["A stool with a seat and a leg."]), ledger(["The stool has a backrest."])])


def run(tmp_path, sessions, m, arms=None):
    return run_experiment([SESSION], arms or {"M0": 1, "M2": 1}, tmp_path / "exp", BUILD, log=lambda x: None,
                          llm_factory=m, session_factory=sessions, ledger_model="ledger")


def test_arms_fork_from_one_base_and_are_scored(tmp_path, sessions):
    m = models()
    summary = run(tmp_path, sessions, m)
    root = tmp_path / "exp" / "stool"
    assert {p.name for p in root.iterdir()} == {"base", "M0_r1", "M2_r1"}
    assert not (root / "M0_r1" / "turns" / "t01").exists()          # turn 1 ran once, in base
    for arm in ("M0", "M2"):
        assert summary[arm]["completion_rate"] == 1.0 and summary[arm]["regression_rate"] == 0.0
        assert summary[arm]["retention_rate"] == 1.0 and summary[arm]["retention_checks"] == 1
    score = json.loads((root / "M2_r1" / "score.json").read_text())
    assert [t["turn"] for t in score["turns"]] == [1, 2] and score["turns"][1]["precondition_failed"] == []
    # the system saw only the requests: no check ids, patterns or rubric questions
    seen = json.dumps({k: v.seen for k, v in m.llms.items()})
    assert "s2_back" not in seen and "SECRET_RUBRIC" not in seen and "touches" not in seen


def test_reruns_cost_nothing_and_crashes_are_isolated(tmp_path, sessions):
    run(tmp_path, sessions, models())
    again = run(tmp_path, sessions, Models())                          # no scripted replies: any call would fail
    assert again["M0"]["editing_turns"] == 1 and not (tmp_path / "exp" / "stool" / "M0_r1" / "crash.json").exists()
    broken = run(tmp_path, sessions, Models(), arms={"M3": 1})        # nothing scripted for M3: it crashes
    assert (tmp_path / "exp" / "stool" / "M3_r1" / "crash.json").exists() and "M3" not in broken


def test_regressions_and_lost_requirements_are_counted(tmp_path, sessions):
    lose_leg = [reply(calls=[call(
        "edit_script", "e1", edits=[{"old_string": 'make_mesh("Leg")\n', "new_string": 'make_mesh("Back")\n'}])]),
        reply(calls=[call(
            "declare_changes", "d1", added=["Back"], modified=[], removed=[])]), reply("done")]
    spec = {**SESSION, "turns": [{**SESSION["turns"][0], "persist": ["s1_seat", "s1_leg"]}, SESSION["turns"][1]]}
    m = Models(coder=coder_builds() + lose_leg, critic=[reply(CRITIC_OK)] * 2, ledger=[ledger(["A stool."])])
    run_experiment([spec], {"M0": 1}, tmp_path / "exp", BUILD, log=lambda x: None, llm_factory=m,
                   session_factory=sessions, ledger_model="ledger")
    s = score_experiment([spec], tmp_path / "exp")["M0"]
    assert s["regression_rate"] == 1.0 and s["retention_rate"] == 0.5     # Leg was removed: regression, lost
    assert aggregate([])["editing_turns"] == 0
