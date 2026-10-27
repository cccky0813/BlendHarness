"""eval/judge.py with a scripted judge: rubric dependencies and majority, scores mean/std, pairwise swap consistency,
Bradley-Terry, what the judge is shown, and caching over an experiment run on fakes."""

import json

from eval.judge import (DIMENSIONS, _message, apply_dependencies, ask_rubric, bradley_terry, compare_swapped,
                        elo_with_ci, judge_pairs, judge_turns, majority, mean_std, turn_view)
from blendharness.core.tracing import Tracer
from tests.fakes import ScriptedLLM, reply
from tests.unit.test_run_sessions import SESSION, models, run
from tests.unit.test_session import sessions  # noqa: F401 (fixture)

QS = [{"id": "Q1", "q": "Is there a bucket?"}, {"id": "Q2", "q": "Is it wooden?", "dep": "Q1"},
      {"id": "Q3", "q": "Is it on the ground?", "dep": "Q2"}, {"id": "P1", "q": "Is the rest unchanged?"}]
VIEW = {"request": "Add a bucket.", "earlier": ["Build a well."], "before": [], "after": [], "diff": "added: Bucket"}


def answers(**a):
    return reply(json.dumps({"answers": [{"id": k, "evidence": "e", "answer": v} for k, v in a.items()]}))


def pair(**w):
    return reply(json.dumps({d: {"evidence": "e", "winner": w.get(d, "tie")} for d in DIMENSIONS}))


def test_children_of_a_failed_question_are_no(tmp_path):
    llm = ScriptedLLM([answers(Q1="no", Q2="yes", Q3="yes", P1="yes")], Tracer(tmp_path))
    got = ask_rubric(llm, VIEW, QS)
    assert [got[q]["answer"] for q in ("Q1", "Q2", "Q3", "P1")] == ["no", "no", "no", "yes"]
    assert got["Q3"]["forced_by"] == "Q2"                    # transitively: Q3 -> Q2 -> Q1
    assert "Q2: Is it wooden?" in json.dumps(llm.seen[0])


def test_majority_and_invalid_answers():
    runs = [apply_dependencies({"Q1": {"answer": a}}, QS[:1]) for a in ("yes", "yes", "no")]
    assert majority(runs)["Q1"] == {"answer": "yes", "agreement": 0.667, "votes": {"yes": 2, "no": 1}}
    s = mean_std([{d: 4 for d in DIMENSIONS}, {**{d: 2 for d in DIMENSIONS}, "geometry": None}])
    assert s["request"] == {"mean": 3, "std": 1.0, "n": 2} and s["geometry"]["n"] == 1


def test_pairwise_counts_a_winner_only_when_both_orders_agree(tmp_path):
    # first order: A wins request and geometry; swapped order: the same system (now B) wins request, but not geometry
    llm = ScriptedLLM([pair(request="A", geometry="A"), pair(request="B", geometry="A")], Tracer(tmp_path))
    v = compare_swapped(llm, {**VIEW, "diff": "x"}, {**VIEW, "diff": "y"})
    assert v["request"] == "A" and v["geometry"] == "tie" and v["materials"] == "tie"
    assert "System A, object changes:\ny" in llm.seen[1][1]["content"][0]["text"]   # the second call swapped sides


def test_bradley_terry_orders_and_bootstraps_over_sessions():
    games = {"s1": [("M2", "M0", 1.0)] * 3 + [("M2", "M0", 0.0)], "s2": [("M2", "M0", 1.0), ("M2", "M0", 0.5)]}
    elo = elo_with_ci(games, n_boot=200)
    assert elo["M2"]["elo"] > 1000 > elo["M0"]["elo"] and elo["M2"]["ci95"][0] <= elo["M2"]["elo"]
    even = bradley_terry([("A", "B", 1.0), ("A", "B", 0.0)])
    assert abs(even["A"] - even["B"]) < 1e-6


def test_images_are_labelled_by_group(tmp_path):
    pngs = []
    for i in range(3):
        p = tmp_path / f"{i}.png"
        p.write_bytes(b"png")
        pngs.append(str(p))
    msg = _message("text", [("BEFORE", pngs[:1]), ("AFTER", pngs[1:])])
    assert "BEFORE: images 1-1" in msg["content"][0]["text"] and "AFTER: images 2-3" in msg["content"][0]["text"]
    assert len(msg["content"]) == 4


def test_judging_an_experiment_is_cached_and_uses_verbatim_requests(tmp_path, sessions):
    run(tmp_path, sessions, models())
    exp = tmp_path / "exp"
    v = turn_view(SESSION, exp / "stool" / "M2_r1", 2)
    assert v["earlier"] == [SESSION["turns"][0]["request"]] and "added: Back" in v["diff"]   # not the M2 ledger
    rubric = [answers(Q1="yes")] * 6
    scores = [reply(json.dumps({d: {"evidence": "e", "score": 4} for d in DIMENSIONS}))] * 6
    judge = ScriptedLLM(rubric[:3] + scores[:3] + rubric[3:] + scores[3:], Tracer(tmp_path))
    out = judge_turns([SESSION], exp, "gpt-x", ["rubric", "scores"], llm_factory=lambda m, t: judge, log=lambda m: None)
    assert out["M0"]["rubric_request"] == 1.0 and out["M2"]["score_request"] == 4 and out["M2"]["std_request"] == 0
    again = judge_turns([SESSION], exp, "gpt-x", ["rubric", "scores"],
                        llm_factory=lambda m, t: ScriptedLLM([], t), log=lambda m: None)    # all cached: no calls
    assert again["M2"] == out["M2"]
    pj = ScriptedLLM([pair(request="A"), pair(request="B")], Tracer(tmp_path))
    res = judge_pairs([SESSION], exp, "gpt-x", "M2", "M0", llm_factory=lambda m, t: pj, log=lambda m: None)
    assert res["n_comparisons"] == 1 and res["wins"]["request"] == {"A": 1}
    assert res["elo"]["request"]["M2"]["elo"] > res["elo"]["request"]["M0"]["elo"]


def test_session_rubrics_are_answerable_from_the_renders():
    """Every dep names a question of the same turn; no question asks a compass direction (the renders have no
    compass: those are programmatic checks). Smoke 2026-10-27: 'a garden EAST of the cottage' as a parent turned
    every child into "no"."""
    import re
    from eval.run_sessions import load_sessions
    for s in load_sessions():
        for k, t in enumerate(s["turns"], 1):
            ids = {q["id"] for q in t.get("rubric", [])}
            for q in t.get("rubric", []):
                assert q.get("dep") in ids | {None}, (s["id"], k, q["id"])
                assert not re.search(r"\b(east|west|north|south)", q["q"], re.I), (s["id"], k, q["q"])


def test_a_failed_turn_is_skipped_not_fatal(tmp_path, sessions):
    run(tmp_path, sessions, models())
    out = judge_turns([SESSION], tmp_path / "exp", "gpt-x", ["scores"], llm_factory=lambda m, t: ScriptedLLM([], t),
                      log=lambda m: None)                                  # every call fails
    assert len(out["_judge"]["errors"]) == 2 and "M2" not in out          # both arms' turn 2 skipped, run finished
