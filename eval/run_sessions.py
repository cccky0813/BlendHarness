"""Multi-turn session experiment (docs/multiturn_design.md 5.7, 6.1, 6.5): sessions x memory strategies x repeats,
scored with the programmatic checks; the LLM judge (eval/judge.py) runs on the same directories afterwards.

    runs/sessions/<exp>/<session>/base/        turn 1, run ONCE per session (structured memory, so it has memory.db)
    runs/sessions/<exp>/<session>/M2_r1/       every arm forks from base@t01 and runs turns 2..n
    runs/sessions/<exp>/<session>/M2_r1/score.json     programmatic checks of every turn (turn 1 from base)
    runs/sessions/<exp>/summary.json, summary.md       one row per arm

All arms start from the same turn-1 scene, so they differ only in what the Coder is told about earlier turns.
The system only ever gets the request strings, one turn at a time (run_session): the session file's `expect`,
`touches`, `persist`, `requires`, `refs` and `rubric` stay here. Resumable: finished turns are skipped, and an arm
that crashed is retried on the next run (its error is kept in crash.json).

    python eval/run_sessions.py --exp pilot                              # all sessions, M0 x2, M2 x2, M3 x1
    python eval/run_sessions.py --exp pilot --sessions village --arms M2 --repeats 1
    python eval/run_sessions.py --exp pilot --jobs 4                     # 4 arms at once
    python eval/run_sessions.py --exp pilot --score-only                 # rescore, no model calls
"""

import argparse
import json
import statistics
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blendharness.analysis.expectations import PASS, evaluate, score_turn  # noqa: E402
from blendharness.core.llm import LLMClient  # noqa: E402
from blendharness.sandbox.session import BlenderSession  # noqa: E402
from blendharness.workflow.build import BuildConfig  # noqa: E402
from blendharness.workflow.session import SessionConfig, run_session  # noqa: E402

SESSIONS_FILE = Path(__file__).with_name("sessions_draft.json")
ARMS = {"M0": "history", "M2": "structured", "M3": "retrieval"}
DEFAULT_REPEATS = {"M0": 2, "M2": 2, "M3": 1}       # design 5.7: M3 answers "how much cheaper", needs fewer samples
SESSION_CODER_BUDGET = 1.0    # smoke 2026-10-27: turns 4 and 6 hit the 0.5 single-build limit (scripts ~250 lines)
BASE_MEMORY = "structured"                          # turn 1 is the same for every arm; this one also writes memory.db


def load_sessions(path: Path = SESSIONS_FILE, ids: list[str] | None = None) -> list[dict]:
    sessions = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    return [s for s in sessions if not ids or s["id"] in ids]


def requests_of(session: dict) -> list[str]:
    return [t["request"] for t in session["turns"]]


# ---------- running ----------
def run_base(session: dict, exp_dir: Path, build: BuildConfig, ledger_model: str = SessionConfig().ledger_model,
             **kwargs) -> Path:
    """Turn 1 of a session, once: every arm forks from it."""
    base = exp_dir / session["id"] / "base"
    run_session(requests_of(session)[:1], SessionConfig(build, memory=BASE_MEMORY, ledger_model=ledger_model), base,
                resume=True, **kwargs)
    return base


def run_arm(session: dict, arm: str, repeat: int, exp_dir: Path, build: BuildConfig,
            ledger_model: str = SessionConfig().ledger_model, **kwargs) -> Path:
    """This arm's fork of base@t01, turns 2..n (the base must have finished). Returns the arm's directory."""
    root = exp_dir / session["id"]
    arm_dir = root / f"{arm}_r{repeat}"
    run_session(requests_of(session)[1:], SessionConfig(build, memory=ARMS[arm], ledger_model=ledger_model), arm_dir,
                resume=True, fork_from=(root / "base", 1), **kwargs)
    return arm_dir


_print_lock = threading.Lock()


def _guarded(name: str, out_dir: Path, fn, log) -> bool:
    """Run one unit of work with its own prefixed log (also written to out_dir/run.log); a crash is recorded in
    out_dir/crash.json and does not stop the others. Returns whether it finished."""
    out_dir.mkdir(parents=True, exist_ok=True)
    logfile = out_dir / "run.log"

    def unit_log(msg: str) -> None:
        with _print_lock:
            log(f"[{name}] {msg}")
            with logfile.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
    try:
        fn(unit_log)
        (out_dir / "crash.json").unlink(missing_ok=True)
        return True
    except Exception as e:          # one broken unit must not stop the experiment
        (out_dir / "crash.json").write_text(json.dumps(
            {"error": repr(e), "traceback": traceback.format_exc()}, indent=1), encoding="utf-8")
        unit_log(f"!!!!! crashed: {e!r}")
        return False


def run_experiment(sessions: list[dict], arms: dict[str, int], exp_dir: Path, build: BuildConfig,
                   log=print, jobs: int = 1, ledger_model: str = SessionConfig().ledger_model, **kwargs) -> dict:
    """Phase 1: every session's base turn. Phase 2: every arm, ordered repeat 1 of everything first (so a partial
    run stays balanced). With jobs > 1 each phase runs that many units at once, in threads: the work is waiting
    on models and Blender, and every unit has its own Blender process, directory and tracer."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    common = dict(build=build, ledger_model=ledger_model, **kwargs)

    def pool(units):
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            return list(ex.map(lambda u: _guarded(*u, log), units))

    finished = pool([(f"{s['id']} base", exp_dir / s["id"] / "base",
                      lambda lg, s=s: run_base(s, exp_dir, log=lg, **common)) for s in sessions])
    ok = {s["id"] for s, done in zip(sessions, finished) if done}
    units = [(f"{s['id']} {arm} r{r}", exp_dir / s["id"] / f"{arm}_r{r}",
              lambda lg, s=s, arm=arm, r=r: run_arm(s, arm, r, exp_dir, log=lg, **common))
             for r in range(1, max(arms.values()) + 1) for s in sessions if s["id"] in ok
             for arm, n in arms.items() if r <= n]
    pool(units)
    return score_experiment(sessions, exp_dir)


# ---------- scoring (no model calls) ----------
def _scene(turn_dir: Path, name: str) -> dict:
    p = turn_dir / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"objects": []}


def score_arm(session: dict, arm_dir: Path) -> dict | None:
    """Every finished turn: its checks, preconditions, regressions; at the end, whether `persist` checks still hold."""
    summary_path = arm_dir / "session.json"
    if not summary_path.exists():
        return None
    record = json.loads(summary_path.read_text(encoding="utf-8"))
    checks = {c["id"]: c for t in session["turns"] for c in t.get("expect", [])}
    earlier: dict = {}
    turns = []
    for t in record["turns"]:
        spec = session["turns"][t["turn"] - 1]
        turn_dir = Path(t["run_dir"])
        before = _scene(turn_dir, "scene_before.json") if t["turn"] > 1 else {"objects": []}
        after = _scene(turn_dir, "scene_after.json")
        s = score_turn(spec, before, after, earlier)
        earlier.update({r.id: r for r in s["results"]})
        result = json.loads((turn_dir / "result.json").read_text(encoding="utf-8")) if (turn_dir / "result.json").exists() else {}
        coder = (result.get("llm_per_agent") or {}).get("coder", {})
        turns.append({"turn": t["turn"], "completed": s["completed"], "precondition_failed": s["precondition_failed"],
                      "undecidable": s["undecidable"], "regressions": s["regressions"], "renamed": s["renamed"],
                      "results": [asdict(r) for r in s["results"]],
                      "approved": t["approved"], "cost_usd": t["cost_usd"],
                      "coder_input_tokens": coder.get("input_tokens"), "coder_calls": coder.get("calls"),
                      "unrequested_changes": t.get("unrequested_changes", []), "undeclared": t.get("undeclared", False),
                      "touched_sections": t.get("touched_sections"), "retrieval": t.get("retrieval"),
                      "ledger": t.get("ledger")})
    final = _scene(Path(record["turns"][-1]["run_dir"]), "scene_after.json") if record["turns"] else {"objects": []}
    retention = []
    done = session["turns"][:len(record["turns"])]
    retired = {cid for t in done for cid in t.get("retires", [])}  # a later turn changed that requirement on purpose
    for t in done[:-1]:                                            # requirements of every turn before the last
        for cid in t.get("persist", []):
            if cid in retired:
                continue
            was = earlier.get(cid)
            if was is None or was.status != PASS:
                continue                                           # never held: nothing to retain
            now = evaluate(checks[cid], final)
            retention.append({"id": cid, "held": now.status == PASS, "status": now.status, "detail": now.detail})
    out = {"session": session["id"], "arm": arm_dir.name, "n_turns": len(record["turns"]),
           "expected_turns": len(session["turns"]), "crashed": (arm_dir / "crash.json").exists(),
           "turns": turns, "retention": retention}
    (arm_dir / "score.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def aggregate(scores: list[dict]) -> dict:
    """One arm: rates over its editing turns (turn >= 2), so turn 1 (shared by every arm) does not dilute them."""
    edits = [t for s in scores for t in s["turns"] if t["turn"] > 1]
    scored = [t for t in edits if not t["precondition_failed"]]
    ret = [r for s in scores for r in s["retention"]]
    recalls = [t["retrieval"]["recall"] for t in edits if t.get("retrieval") and t["retrieval"].get("recall") is not None]
    return {
        "sessions": len(scores), "editing_turns": len(edits),
        "finished": sum(s["n_turns"] == s["expected_turns"] for s in scores),
        "completion_rate": _mean([t["completed"] for t in scored]),            # every check of the turn passed
        "precondition_failed": sum(bool(t["precondition_failed"]) for t in edits),
        "regression_rate": _mean([bool(t["regressions"]) for t in edits]),    # turns that changed a protected object
        "regressions_per_turn": _mean([len(t["regressions"]) for t in edits]),
        "retention_rate": _mean([r["held"] for r in ret]), "retention_checks": len(ret),
        "undecidable_checks": sum(t["undecidable"] for t in edits),
        "critic_approved_rate": _mean([t["approved"] for t in edits]),
        "unrequested_per_turn": _mean([len(t["unrequested_changes"]) for t in edits if not t["undeclared"]]),
        "undeclared_turns": sum(t["undeclared"] for t in edits),        # stopped before declare_changes
        "cost_per_turn": _mean([t["cost_usd"] for t in edits]),
        "coder_input_tokens_per_turn": _mean([t["coder_input_tokens"] for t in edits]),
        "retrieval_recall": _mean(recalls),
    }


def score_experiment(sessions: list[dict], exp_dir: Path) -> dict:
    by_arm: dict[str, list[dict]] = {}
    for session in sessions:
        root = exp_dir / session["id"]
        for arm_dir in sorted(root.glob("M*_r*")) if root.exists() else []:
            s = score_arm(session, arm_dir)
            if s is not None:
                by_arm.setdefault(arm_dir.name.split("_")[0], []).append(s)
    summary = {arm: aggregate(scores) for arm, scores in sorted(by_arm.items())}
    (exp_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (exp_dir / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


ROWS = [("completion_rate", "单轮完成率"), ("regression_rate", "回归率（有回归的轮次占比）"),
        ("regressions_per_turn", "每轮回归物体数"), ("retention_rate", "需求保持率"),
        ("critic_approved_rate", "Critic 通过率"), ("unrequested_per_turn", "每轮非请求改动数"),
        ("undeclared_turns", "未声明就停下的轮数"), ("cost_per_turn", "每轮成本 $"), ("coder_input_tokens_per_turn", "每轮 Coder 输入 token"),
        ("retrieval_recall", "检索 recall（M3）"), ("editing_turns", "编辑轮数"),
        ("precondition_failed", "前置失败轮数"), ("undecidable_checks", "无法判定的检查")]


def render_summary(summary: dict) -> str:
    arms = list(summary)
    lines = ["| 指标 | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for key, label in ROWS:
        lines.append(f"| {label} | " + " | ".join("-" if summary[a].get(key) is None else str(summary[a][key])
                                                  for a in arms) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True, help="experiment name: runs/sessions/<exp>")
    ap.add_argument("--sessions", nargs="*", help="session ids (default: all)")
    ap.add_argument("--arms", nargs="*", choices=list(ARMS), default=list(ARMS))
    ap.add_argument("--repeats", type=int, help="same repeat count for every arm (default: M0 2, M2 2, M3 1)")
    ap.add_argument("--file", type=Path, default=SESSIONS_FILE)
    ap.add_argument("--coder-model", default=BuildConfig().coder_model)
    ap.add_argument("--rounds", type=int, default=BuildConfig().rounds)
    ap.add_argument("--coder-budget", type=float, default=SESSION_CODER_BUDGET,
                    help="$ per Coder run (single builds use 0.5; session scripts grow, so edits cost more)")
    ap.add_argument("--jobs", type=int, default=1, help="arms run at once (each has its own Blender process)")
    ap.add_argument("--score-only", action="store_true")
    args = ap.parse_args()
    sessions = load_sessions(args.file, args.sessions)
    exp_dir = Path("runs/sessions") / args.exp
    if args.score_only:
        summary = score_experiment(sessions, exp_dir)
    else:
        arms = {a: args.repeats or DEFAULT_REPEATS[a] for a in args.arms}
        summary = run_experiment(sessions, arms, exp_dir, BuildConfig(coder_model=args.coder_model, rounds=args.rounds,
                                                                      coder_max_cost_usd=args.coder_budget),
                                 jobs=args.jobs, llm_factory=LLMClient, session_factory=BlenderSession)
    print(render_summary(summary))


if __name__ == "__main__":
    main()
