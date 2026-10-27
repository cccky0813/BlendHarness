"""Multi-turn sessions on fakes: turns build on each other, the system never sees later turns, the Coder must
declare its changes, and a session resumes or forks after any finished turn."""

import json

import pytest

from blendharness.workflow.build import BuildConfig
from blendharness.workflow.session import Session, SessionConfig, run_session, turn_texts
from tests.fakes import call, reply
from tests.unit.test_build import CRITIC_OK, SEAT, Models, coder_builds

CFG = SessionConfig(BuildConfig(coder_model="coder", critic_model="critic", rounds=1))
T1, T2, T3 = "Build a stool.", "Add a backrest to the stool.", "Add a cushion on the seat."


def coder_edits(old: str, new: str, added: list[str]):
    return [reply(calls=[call("edit_script", "e1", edits=[{"old_string": old, "new_string": new}])]),
            reply(calls=[call("declare_changes", "d1", added=added, modified=[], removed=[])]),
            reply("done")]


ADD_BACK = coder_edits('make_mesh("Leg")\n', 'make_mesh("Leg")\nmake_mesh("Back")\n', ["Back"])
ADD_CUSHION = coder_edits('make_mesh("Seat")\n', 'make_mesh("Seat")\nmake_mesh("Cushion")\n', ["Cushion"])


@pytest.fixture
def sessions():
    from tests.fakes import FakeSession
    return lambda log_path: FakeSession(log_path)


def run(tmp_path, sessions, models, requests, **kw):
    return run_session(requests, CFG, tmp_path / "s", log=kw.pop("log", lambda m: None), llm_factory=models,
                       session_factory=sessions, **kw)


def test_turns_build_on_the_previous_scene(tmp_path, sessions):
    models = Models(coder=coder_builds() + ADD_BACK, critic=[reply(CRITIC_OK), reply(CRITIC_OK)])
    summary = run(tmp_path, sessions, models, [T1, T2])
    assert summary["n_turns"] == 2 and summary["approved_turns"] == 2
    t2 = tmp_path / "s" / "turns" / "t02"
    assert 'make_mesh("Back")' in (t2 / "final_script.py").read_text() and SEAT.strip() in (t2 / "final_script.py").read_text()
    before = [o["name"] for o in json.loads((t2 / "scene_before.json").read_text())["objects"]]
    after = [o["name"] for o in json.loads((t2 / "scene_after.json").read_text())["objects"]]
    assert before == ["Seat", "Leg"] and after == ["Seat", "Leg", "Back"]
    assert summary["turns"][1]["declared_changes"] == [{"added": ["Back"], "modified": [], "removed": [], "note": ""}]
    # turn 2's coder got the turn-2 rules with turn 1 as history, and edited the pinned script (no rebuild)
    first_view = models.llms["coder"].seen[2]
    task = next(m["content"] for m in first_view if m["role"] == "user")
    assert "turn 2" in task and "1. " + T1 in task and T2 in task and "declare_changes" in task
    assert any("CURRENT WORKING SCRIPT" in str(m.get("content")) for m in first_view)


def test_the_system_never_sees_later_turns(tmp_path, sessions):
    models = Models(coder=coder_builds() + ADD_BACK + ADD_CUSHION, critic=[reply(CRITIC_OK)] * 3)
    seen_before_t3 = {}

    def log(msg):
        if msg.startswith("=== turn 3"):
            seen_before_t3.update({m: list(llm.seen) for m, llm in models.llms.items()})
    run(tmp_path, sessions, models, [T1, T2, T3], log=log)
    assert seen_before_t3
    for views in seen_before_t3.values():
        assert T3 not in json.dumps(views)


def test_finishing_without_declaring_is_nudged(tmp_path, sessions):
    lazy = [reply(calls=[call("edit_script", "e1", edits=[{"old_string": 'make_mesh("Leg")\n',
                                                          "new_string": 'make_mesh("Leg")\nmake_mesh("Back")\n'}])]),
            reply("done"),                                                     # forgot to declare
            reply(calls=[call("declare_changes", "d1", added=["Back"], modified=[], removed=[])]), reply("done")]
    models = Models(coder=coder_builds() + lazy, critic=[reply(CRITIC_OK)] * 2)
    run(tmp_path, sessions, models, [T1, T2])
    nudges = [m["content"] for v in models.llms["coder"].seen for m in v if m["role"] == "user"
              and "declare_changes" in str(m["content"]) and "Not finished yet" in str(m["content"])]
    assert nudges


def test_resume_skips_finished_turns_and_checks_their_requests(tmp_path, sessions):
    run(tmp_path, sessions, Models(coder=coder_builds(), critic=[reply(CRITIC_OK)]), [T1])
    models = Models(coder=ADD_BACK, critic=[reply(CRITIC_OK)])                 # only turn 2 is scripted
    summary = run(tmp_path, sessions, models, [T1, T2], resume=True)
    assert summary["n_turns"] == 2
    with pytest.raises(ValueError):
        run(tmp_path, sessions, Models(), ["Build a table.", T2], resume=True)


def test_fork_continues_from_any_turn(tmp_path, sessions):
    run(tmp_path, sessions, Models(coder=coder_builds() + ADD_BACK, critic=[reply(CRITIC_OK)] * 2), [T1, T2])
    fork = Session(tmp_path / "f", CFG, log=lambda m: None, llm_factory=Models(coder=ADD_CUSHION, critic=[reply(CRITIC_OK)]),
                   session_factory=sessions, fork_from=(tmp_path / "s", 1))
    rec = fork.turn(T3)
    assert rec["turn"] == 2 and fork.state.turns[0]["request"] == T1
    script = fork.state.current_script
    assert 'make_mesh("Cushion")' in script and 'make_mesh("Back")' not in script


def test_turn_texts_and_unknown_memory():
    assert turn_texts([], T1, "history") == (T1, T1)
    task, review = turn_texts([T1], T2, "history")
    assert T2 in review and "1. " + T1 in review and "Judge mainly" in review
    with pytest.raises(NotImplementedError):
        turn_texts([T1], T2, "structured")


def test_a_resumed_fork_numbers_its_turns_after_the_fork_point(tmp_path, sessions):
    models = Models(coder=coder_builds() + ADD_BACK + ADD_CUSHION, critic=[reply(CRITIC_OK)] * 3)
    run(tmp_path, sessions, models, [T1, T2])
    fork = tmp_path / "fork"
    kw = dict(log=lambda m: None, llm_factory=models, session_factory=sessions)
    run_session([T3], CFG, fork, fork_from=(tmp_path / "s", 1), **kw)
    again = run_session([T3], CFG, fork, fork_from=(tmp_path / "s", 1), resume=True, **kw)   # nothing left to run
    assert [t["request"] for t in again["turns"]] == [T1, T3]
