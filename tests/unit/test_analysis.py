"""Trajectory analysis: issue lifecycle, process metrics, classifier caching, and the real file format."""

import json

from blendharness.analysis import IssueClassifier, aggregate, find_runs, keyword_category, load_trajectory, run_metrics
from blendharness.analysis.trajectory import _issue_lifecycle
from blendharness.workflow.build import BuildConfig, run_build
from tests.fakes import FakeSession, reply
from tests.unit.test_build import CRITIC_ISSUE, VERIFIED, Models, coder_adds_backrest, coder_builds


# ---------- issue lifecycle ----------
def test_issue_lifecycle_follows_verifier_indices_and_regressions():
    rounds = [
        {"round": 0, "issues": [{"problem": "A floats"}, {"problem": "B too big"}, {"problem": "C wrong color"}]},
        {"round": 1, "verdicts": [{"index": 1, "verdict": "FIXED"}, {"index": 2, "verdict": "PARTIAL"},
                                  {"index": 3, "verdict": "NOT_FIXED"}], "regressions": ["D now sunk"]},
        # open now: [B, C, D] -> index 1 = B, 2 = C, 3 = D
        {"round": 2, "verdicts": [{"index": 1, "verdict": "FIXED"}, {"index": 2, "verdict": "NOT_FIXED"},
                                  {"index": 3, "verdict": "FIXED"}], "regressions": []},
    ]
    issues = {i.text: i for i in _issue_lifecycle(rounds)}
    assert issues["A floats"].verdicts == ["FIXED"] and issues["A floats"].fixed
    assert issues["B too big"].verdicts == ["PARTIAL", "FIXED"] and issues["B too big"].fixed
    assert issues["C wrong color"].verdicts == ["NOT_FIXED", "NOT_FIXED"] and not issues["C wrong color"].fixed
    assert issues["D now sunk"].regression and issues["D now sunk"].raised_in == 1 and issues["D now sunk"].fixed


def test_verified_round_without_details_counts_all_open_issues_fixed():
    rounds = [{"round": 0, "issues": [{"problem": "x"}, {"problem": "y"}]}, {"round": 1, "verified": True}]
    assert all(i.fixed for i in _issue_lifecycle(rounds))


# ---------- on a real (fake-backed) build ----------
def test_load_trajectory_from_a_pipeline_run(tmp_path):
    run = tmp_path / "runs" / "eval" / "exp1" / "m3" / "chair_r1"
    models = Models(coder=coder_builds() + coder_adds_backrest(), critic=[reply(CRITIC_ISSUE), reply(VERIFIED)])
    run_build("a chair", BuildConfig(coder_model="coder", critic_model="critic", rounds=2), run,
              log=lambda m: None, llm_factory=models, session_factory=lambda p: FakeSession(p))
    assert list(find_runs(tmp_path / "runs")) == [run]
    t = load_trajectory(run, tmp_path / "runs")
    assert (t.experiment, t.variant, t.task_id) == ("exp1", "m3", "chair")
    assert t.approved and not t.approved_in_round0 and t.n_rounds == 2
    assert [r.round for r in t.coder_runs] == [0, 1] and [r.status for r in t.coder_runs] == ["done", "done"]
    assert [s.name for s in t.coder_runs[0].tools] == ["execute_bpy"]
    assert [(i.text, i.verdicts) for i in t.issues] == [("no backrest", ["FIXED"])]
    m = run_metrics(t)
    assert (m["exec_attempts"], m["exec_failures"], m["issues_raised"]) == (2, 0, 1)


# ---------- process metrics ----------
def write_run(root, name, result, spans, payloads=None):
    d = root / name
    (d / "payloads").mkdir(parents=True)
    (d / "result.json").write_text(json.dumps(result))
    (d / "trace.jsonl").write_text("\n".join(json.dumps(s) for s in spans) + "\n")
    for span_id, response in (payloads or {}).items():
        (d / "payloads" / f"{span_id}.json").write_text(json.dumps({"response": response}))
    return d


def span(kind, name, span_id, parent=None, t=0.0, **attrs):
    return {"kind": kind, "name": name, "span_id": span_id, "parent_id": parent, "start_time": t, "attrs": attrs}


def test_recovery_hidden_thinking_and_guard_blocks(tmp_path):
    spans = [
        span("agent", "coder", "a", t=0, status="done", steps=3),
        span("llm", "m", "l1", "a", t=1, output_tokens=1000, cost_usd=0.1),
        span("tool", "execute_bpy", "t1", "a", t=2, is_error=True, output_preview="ERROR: blocked by the sandbox policy: x"),
        span("tool", "execute_bpy", "t2", "a", t=3, is_error=True, output_preview="ERROR: KeyError"),
        span("tool", "execute_bpy", "t3", "a", t=4, is_error=False, output_preview="OK: script ran"),
    ]
    result = {"prompt": "p", "approved": True, "status": "approved", "rounds": [{"round": 0, "critic_approved": True}],
              "llm_total": {"cost_usd": 0.1}}
    visible = {"content": "", "tool_calls": [{"function": {"arguments": "x" * 230}}]}     # = 100 tokens
    d = write_run(tmp_path, "r1", result, spans, payloads={"l1": visible})
    t = load_trajectory(d, tmp_path)
    run = t.coder_runs[0]
    assert run.recovered is True and run.hidden_tokens_est == 900
    m = run_metrics(t)
    assert (m["exec_failures"], m["guard_blocks"], m["hidden_share"]) == (2, 1, 0.9)
    s = aggregate([t])
    assert s["process"]["recovery_rate"] == 1.0 and s["outcome"]["pass_round0"] == 1.0


# ---------- classifier ----------
def test_classifier_caches_llm_labels_but_not_fallbacks(tmp_path):
    cache = tmp_path / "labels.json"
    texts = ["The lamp floats above the table", "The roof is far too big"]
    offline = IssueClassifier(cache)                                     # no LLM: keywords, nothing cached
    assert offline.classify(texts) == ["floating", "proportion"] and not cache.exists()

    calls = []
    def ask(prompt):
        calls.append(prompt)
        return '{"1": "placement", "2": "proportion"}'
    online = IssueClassifier(cache, ask=ask)
    assert online.classify(texts) == ["placement", "proportion"]
    assert IssueClassifier(cache, ask=ask).classify(texts) == ["placement", "proportion"]
    assert len(calls) == 1                                               # second time: from the cache


def test_invalid_llm_reply_falls_back_to_keywords(tmp_path):
    clf = IssueClassifier(tmp_path / "labels.json", ask=lambda p: "sorry, no JSON")
    assert clf.classify(["The chair is missing a leg"]) == [keyword_category("The chair is missing a leg")]
