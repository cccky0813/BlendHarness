"""Structured session memory (M2): the SQLite store, the code-generated registry and episodes, the ledger keeper's
gates, and a structured session on fakes (resume restarts from the previous turn's memory, a fork copies it)."""

import json

from blendharness.agents.ledger_keeper import check_proposal, update_ledger
from blendharness.memory.session import (SessionMemory, episode_line, registry_rows, render_ledger,
                                         render_registry)
from blendharness.memory.session.views import group_of
from blendharness.workflow.build import BuildConfig
from blendharness.workflow.session import Session, SessionConfig, run_session
from tests.fakes import ScriptedLLM, reply
from tests.unit.test_build import CRITIC_OK, Models, coder_builds
from tests.unit.test_session import ADD_BACK, ADD_CUSHION, T1, T2, T3, sessions  # noqa: F401 (fixture)

CFG = SessionConfig(BuildConfig(coder_model="coder", critic_model="critic", rounds=1), memory="structured",
                    ledger_model="ledger")


def ledger(add, supersede=(), references=()):
    return reply(json.dumps({"add": add, "supersede": list(supersede), "references": list(references)}))


def obj(name, lo=(0, 0, 0), hi=(1, 1, 1), colors=()):
    return {"name": name, "type": "MESH", "bbox": {"min": list(lo), "max": list(hi)}, "material_colors": list(colors)}


# ---------- store ----------
def test_ledger_is_append_only_and_copies_restore(tmp_path):
    mem = SessionMemory(tmp_path / "m.db")
    r1, r2 = mem.add_requirement(1, "A stool."), mem.add_requirement(2, "The seat is red.")
    r3 = mem.add_requirement(3, "The seat is blue.")
    mem.supersede(r2, r3, "user changed the color")
    assert [r["id"] for r in mem.requirements(active_only=True)] == [r1, r3]
    assert [r["status"] for r in mem.requirements()] == ["active", "superseded", "active"]
    mem.save_copy(tmp_path / "copy.db")
    mem.add_requirement(4, "later")
    mem.close()
    back = SessionMemory.restore(tmp_path / "copy.db", tmp_path / "m.db")
    assert len(back.requirements()) == 3                      # the copy, not the later state
    assert SessionMemory.restore(None, tmp_path / "m.db").requirements() == []


# ---------- views ----------
def test_group_names():
    assert [group_of(n) for n in ("Cottage_Roof", "WellBucket", "Tree2_Fol0", "lamp.001")] == \
           ["Cottage", "Well", "Tree", "Lamp"]


def test_registry_tracks_when_objects_were_added_and_changed():
    t1 = registry_rows({"objects": [obj("Seat"), obj("Leg")]}, [], 1, [], "r1")
    assert {r["name"]: (r["added_turn"], r["requirement"]) for r in t1} == {"Seat": (1, "r1"), "Leg": (1, "r1")}
    snap2 = {"objects": [obj("Seat", colors=[[1, 0, 0, 1]]), obj("Leg"), obj("Back", (0, 0, 1), (1, 0.1, 2)),
                         {"name": "Camera", "type": "CAMERA"}]}
    t2 = {r["name"]: r for r in registry_rows(snap2, t1, 2, [{"added": ["Back"]}], "r2")}
    assert set(t2) == {"Seat", "Leg", "Back"}                 # meshes only
    assert (t2["Seat"]["added_turn"], t2["Seat"]["changed_turn"]) == (1, 2)      # recolored
    assert t2["Leg"]["changed_turn"] == 1 and t2["Back"]["requirement"] == "r2"
    text = render_registry(list(t2.values()))
    assert "Seat" in text and "added turn 1, last changed turn 2" in text


def test_ledger_and_episode_texts():
    reqs = [{"id": "r1", "turn": 1, "text": "A stool.", "status": "active"},
            {"id": "r2", "turn": 2, "text": "Red seat.", "status": "superseded"}]
    assert render_ledger(reqs) == "[r1, turn 1] A stool."
    line = episode_line(2, T2, [{"added": ["Back"], "modified": ["Seat"]}], True, 1)
    assert "added Back" in line and "modified Seat" in line and "1 unrequested change" in line


# ---------- ledger keeper ----------
def test_proposal_gates():
    active, names = {"r1", "r2"}, {"Seat", "Leg"}
    ok, problems = check_proposal({"add": ["Blue seat."], "supersede": [{"id": "r2", "by": 0, "reason": "recolor"}],
                                   "references": [{"phrase": "the seat", "objects": ["Seat", "Ghost"]}]}, active, names)
    assert not problems and ok.references == [{"phrase": "the seat", "objects": ["Seat"]}]   # invented name dropped
    for bad in ({"add": []}, {"add": ["x"], "supersede": [{"id": "r9", "by": 0, "reason": "?"}]},
                {"add": ["x"], "supersede": [{"id": "r1", "by": 0, "reason": ""}]},
                {"add": ["x"], "supersede": [{"id": "r1", "by": 3, "reason": "r"}]}, {"add": ["x" * 400]}, None):
        assert check_proposal(bad, active, names)[1], bad


def test_bad_proposals_are_retried_once_then_fall_back_to_the_request(tmp_path):
    from blendharness.core.tracing import Tracer
    tracer = Tracer(tmp_path)
    fixed = update_ledger(ScriptedLLM([reply("not json"), ledger(["A stool."])], tracer), "", "", "", T1, set(), set())
    assert fixed.add == ["A stool."] and fixed.attempts == 2 and not fixed.fallback
    llm = ScriptedLLM([reply("nope"), ledger([], [{"id": "r7", "by": None, "reason": "x"}])], tracer)
    fell = update_ledger(llm, "", "", "", T2, {"r1"}, set())
    assert fell.fallback and fell.add == [T2] and fell.supersede == []
    assert "Fix these problems" in llm.seen[1][-1]["content"]


# ---------- structured sessions ----------
def structured_models(n_turns=2):
    turns = coder_builds() + ADD_BACK + ADD_CUSHION
    led = [ledger(["The scene is a stool with a seat and a leg."]),
           ledger(["The stool has a backrest named Back."], references=[{"phrase": "the stool", "objects": ["Seat", "Leg"]}]),
           ledger(["A cushion sits on the seat."])]
    return Models(coder=turns, critic=[reply(CRITIC_OK)] * n_turns, ledger=led[:n_turns])


def run(tmp_path, sessions, models, requests, cfg=CFG, **kw):
    return run_session(requests, cfg, kw.pop("dir", tmp_path / "s"), log=lambda m: None, llm_factory=models,
                       session_factory=sessions, **kw)


def coder_task(models, turn_index):
    """The task text of the coder's first call of a turn (turn 1 = index 0)."""
    tasks = [next(m["content"] for m in v if m["role"] == "user") for v in models.llms["coder"].seen]
    return list(dict.fromkeys(tasks))[turn_index]


def test_structured_turn_sees_ledger_registry_and_references(tmp_path, sessions):
    models = structured_models()
    summary = run(tmp_path, sessions, models, [T1, T2])
    assert summary["memory"] == "structured" and summary["approved_turns"] == 2
    task = coder_task(models, 1)
    assert "[r1, turn 1] The scene is a stool" in task           # the ledger, not the raw history
    assert "- Seat:" in task and "- Leg:" in task                 # the registry, built from turn 1's scene
    assert "Turn 1:" in task and '"the stool" means: Seat, Leg' in task
    assert "The stool has a backrest" not in task                 # this turn's own requirement is the request itself
    mem = SessionMemory(tmp_path / "s" / "checkpoints" / "memory_t02.db")
    assert [r["text"] for r in mem.requirements()][-1] == "The stool has a backrest named Back."
    assert {o["name"]: o["added_turn"] for o in mem.objects()} == {"Seat": 1, "Leg": 1, "Back": 2}
    assert len(mem.episodes()) == 2
    assert summary["turns"][1]["ledger"]["added"] == ["r2"]


def test_structured_resume_does_not_add_requirements_twice(tmp_path, sessions):
    run(tmp_path, sessions, structured_models(1), [T1])
    # Pretend turn 2 crashed after the ledger update: memory.db already has r2, but turn 2 never finished.
    mem = SessionMemory(tmp_path / "s" / "memory.db")
    mem.add_requirement(2, "half-done")
    mem.close()
    models = Models(coder=ADD_BACK, critic=[reply(CRITIC_OK)], ledger=[ledger(["The stool has a backrest."])])
    run(tmp_path, sessions, models, [T1, T2], resume=True)
    texts = [r["text"] for r in SessionMemory(tmp_path / "s" / "checkpoints" / "memory_t02.db").requirements()]
    assert texts == ["The scene is a stool with a seat and a leg.", "The stool has a backrest."]


def test_structured_fork_starts_from_the_branch_turns_memory(tmp_path, sessions):
    run(tmp_path, sessions, structured_models(2), [T1, T2])
    models = Models(coder=ADD_CUSHION, critic=[reply(CRITIC_OK)], ledger=[ledger(["A cushion sits on the seat."])])
    run(tmp_path, sessions, models, [T3], dir=tmp_path / "fork", fork_from=(tmp_path / "s", 1))
    texts = [r["text"] for r in SessionMemory(tmp_path / "fork" / "checkpoints" / "memory_t02.db").requirements()]
    assert texts == ["The scene is a stool with a seat and a leg.", "A cushion sits on the seat."]   # no backrest
