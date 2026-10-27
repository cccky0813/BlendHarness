"""LLM judge for multi-turn sessions (docs/multiturn_design.md 6.2), on the directories eval/run_sessions.py wrote.

Three modes, every one OFFLINE (the judge's verdicts never reach the system, so it cannot be optimised against):

    rubric     each turn's atomic yes/no questions (session file `rubric`), with dependencies: when a parent is
               "no", its children are "no" without asking (DSG). Majority over repeats; agreement is reported.
    scores     six 1-5 dimensions with anchors, evidence before score (G-Eval / Prometheus). Mean and std over
               repeats; no total score (it would hide "did the request but broke something else").
    pairwise   two arms, same session and turn, per dimension A / B / tie; asked twice with the sides swapped and a
               verdict only counts when both orders agree (position bias, MT-Bench). Bradley-Terry -> Elo per
               dimension, bootstrap over sessions for a 95% interval (GPTEval3D).

The judge sees, for a turn: the request, the earlier requests VERBATIM (the same for every arm: a strategy's own
ledger would leak the strategy into the judgment), renders before and after, and the object-level diff. Use a
model from another family than the Coder (Sonnet) and Critic (Haiku): GPT or Gemini (`JUDGE_*` in .env).

Verdicts are cached next to each turn (turns/tNN/judge/<mode>__<model>.json); rerunning only pays for new turns.

    python eval/judge.py --exp pilot --model gpt-5 --modes rubric scores
    python eval/judge.py --exp pilot --model gpt-5 --pairwise M0 M2
"""

import argparse
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blendharness.core.llm import LLMClient, image_message  # noqa: E402
from blendharness.core.tracing import Tracer  # noqa: E402
from blendharness.tools.scene import diff_snapshots  # noqa: E402
from eval.run_sessions import SESSIONS_FILE, load_sessions  # noqa: E402

PROMPTS = Path(__file__).with_name("judge_prompts")
DIMENSIONS = ("request", "preserved", "restraint", "plausibility", "geometry", "materials")
REPEATS = 3


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# ---------- what the judge sees ----------
def _turn_dirs(arm_dir: Path) -> list[Path]:
    record = json.loads((arm_dir / "session.json").read_text(encoding="utf-8"))
    return [Path(t["run_dir"]) for t in record["turns"]]


def final_renders(turn_dir: Path) -> list[str]:
    """Renders of the scene the turn ended with (its last round), the top view last; files that exist only."""
    p = turn_dir / "result.json"
    rounds = json.loads(p.read_text(encoding="utf-8")).get("rounds", []) if p.exists() else []
    renders = rounds[-1].get("renders", []) if rounds else []
    return [r for r in renders if Path(r).exists()]


def diff_text(turn_dir: Path) -> str:
    load = lambda n: json.loads((turn_dir / n).read_text(encoding="utf-8")) if (turn_dir / n).exists() else {"objects": []}  # noqa: E731
    d = diff_snapshots(load("scene_before.json"), load("scene_after.json"))
    if "error" in d:
        return "(no diff)"
    changed = [c["name"] for c in d["changed"]]
    return (f"added: {', '.join(d['added']) or 'none'}\nremoved: {', '.join(d['removed']) or 'none'}\n"
            f"changed: {', '.join(changed) or 'none'}")


def turn_view(session: dict, arm_dir: Path, k: int, before_views: int | None = None) -> dict:
    """Everything the judge is shown about turn k (k >= 2) of one arm."""
    dirs = _turn_dirs(arm_dir)
    before = final_renders(dirs[k - 2])
    if before_views is not None and len(before) > before_views:
        before = before[:before_views - 1] + before[-1:]          # keep the top view
    return {"request": session["turns"][k - 1]["request"],
            "earlier": [t["request"] for t in session["turns"][:k - 1]],
            "before": before, "after": final_renders(dirs[k - 1]), "diff": diff_text(dirs[k - 1])}


def _context(v: dict) -> str:
    earlier = "\n".join(f"{i}. {r}" for i, r in enumerate(v["earlier"], 1))
    return f"CURRENT request:\n{v['request']}\n\nEarlier requests, oldest first:\n{earlier or '(none)'}"


def _message(text: str, groups: list[tuple[str, list[str]]]) -> dict:
    """One user message: the text, then each group of images announced by a label line."""
    images, labels = [], []
    for label, paths in groups:
        labels.append(f"{label}: images {len(images) + 1}-{len(images) + len(paths)}" if paths else f"{label}: (none)")
        images += paths
    return image_message(text + "\n\nImages, in order:\n" + "\n".join(labels), images)


# ---------- rubric ----------
def ask_rubric(llm: LLMClient, v: dict, questions: list[dict]) -> dict[str, dict]:
    qs = "\n".join(f"{q['id']}: {q['q']}" for q in questions)
    msg = _message(f"{_context(v)}\n\nObject changes this turn:\n{v['diff']}\n\nQuestions:\n{qs}",
                   [("BEFORE", v["before"]), ("AFTER", v["after"])])
    data = parse_json(llm.chat([{"role": "system", "content": prompt("rubric")}, msg], agent="judge_rubric").text) or {}
    got = {a.get("id"): a for a in data.get("answers", []) if isinstance(a, dict)}
    out = {}
    for q in questions:
        a = got.get(q["id"], {})
        ans = str(a.get("answer", "")).strip().lower()
        out[q["id"]] = {"answer": ans if ans in ("yes", "no") else "invalid", "evidence": a.get("evidence", "")}
    return apply_dependencies(out, questions)


def apply_dependencies(answers: dict[str, dict], questions: list[dict]) -> dict[str, dict]:
    """A child of a parent that is not "yes" is "no" (DSG): no "there is no bucket, but the bucket is wooden"."""
    deps = {q["id"]: q.get("dep") for q in questions}

    def ok(qid: str, seen=()) -> bool:
        parent = deps.get(qid)
        if parent is None or parent in seen:
            return True
        return answers.get(parent, {}).get("answer") == "yes" and ok(parent, seen + (qid,))

    return {qid: (a if ok(qid) else {**a, "answer": "no", "forced_by": deps[qid]}) for qid, a in answers.items()}


def majority(runs: list[dict[str, dict]]) -> dict[str, dict]:
    """Per question: the most common answer over repeats, and how many repeats agreed with it."""
    out = {}
    for qid in runs[0]:
        votes = Counter(r[qid]["answer"] for r in runs)
        answer, n = votes.most_common(1)[0]
        out[qid] = {"answer": answer, "agreement": round(n / len(runs), 3), "votes": dict(votes)}
    return out


# ---------- scores ----------
def ask_scores(llm: LLMClient, v: dict) -> dict[str, int | None]:
    msg = _message(f"{_context(v)}\n\nObject changes this turn:\n{v['diff']}",
                   [("BEFORE", v["before"]), ("AFTER", v["after"])])
    data = parse_json(llm.chat([{"role": "system", "content": prompt("scores")}, msg], agent="judge_scores").text) or {}
    out = {}
    for d in DIMENSIONS:
        s = (data.get(d) or {}).get("score") if isinstance(data.get(d), dict) else None
        out[d] = int(s) if isinstance(s, (int, float)) and 1 <= s <= 5 else None
    return out


def mean_std(runs: list[dict]) -> dict[str, dict]:
    out = {}
    for d in DIMENSIONS:
        xs = [r[d] for r in runs if r.get(d) is not None]
        out[d] = {"mean": round(statistics.mean(xs), 3) if xs else None,
                  "std": round(statistics.pstdev(xs), 3) if len(xs) > 1 else 0.0 if xs else None, "n": len(xs)}
    return out


# ---------- pairwise ----------
def ask_pair(llm: LLMClient, a: dict, b: dict) -> dict[str, str]:
    text = (f"{_context(a)}\n\nSystem A, object changes:\n{a['diff']}\n\nSystem B, object changes:\n{b['diff']}")
    msg = _message(text, [("A BEFORE", a["before"]), ("A AFTER", a["after"]),
                          ("B BEFORE", b["before"]), ("B AFTER", b["after"])])
    data = parse_json(llm.chat([{"role": "system", "content": prompt("pairwise")}, msg], agent="judge_pairwise").text) or {}
    out = {}
    for d in DIMENSIONS:
        w = str((data.get(d) or {}).get("winner", "")).strip().upper() if isinstance(data.get(d), dict) else ""
        out[d] = {"A": "A", "B": "B", "TIE": "tie"}.get(w, "invalid")
    return out


def compare_swapped(llm: LLMClient, a: dict, b: dict) -> dict[str, str]:
    """Ask A-vs-B and B-vs-A; a dimension has a winner only when both orders name the same system."""
    first, second = ask_pair(llm, a, b), ask_pair(llm, b, a)
    flip = {"A": "B", "B": "A", "tie": "tie", "invalid": "invalid"}
    out = {}
    for d in DIMENSIONS:
        x, y = first[d], flip[second[d]]
        out[d] = x if x == y and x != "invalid" else "tie" if "invalid" not in (x, y) else "invalid"
    return out


def bradley_terry(games: list[tuple[str, str, float]], iters: int = 200) -> dict[str, float]:
    """games: (player_i, player_j, score of i: 1 win, 0.5 tie, 0 loss). Minorization-maximization fit of the
    strengths, returned on the Elo scale (400 log10, mean 1000)."""
    players = sorted({p for g in games for p in g[:2]})
    if not players:
        return {}
    wins, n = defaultdict(float), defaultdict(float)
    for i, j, s in games:
        wins[i] += s
        wins[j] += 1 - s
        n[(i, j)] += 1
        n[(j, i)] += 1
    p = {x: 1.0 for x in players}
    for _ in range(iters):
        new = {}
        for i in players:
            denom = sum(n[(i, j)] / (p[i] + p[j]) for j in players if j != i and n[(i, j)])
            new[i] = max(wins[i], 1e-3) / denom if denom else p[i]     # a floor keeps a winless player finite
        g = math.exp(statistics.mean(math.log(v) for v in new.values()))
        p = {k: v / g for k, v in new.items()}
    return {k: round(1000 + 400 * math.log10(v), 1) for k, v in p.items()}


def elo_with_ci(games_by_session: dict[str, list[tuple[str, str, float]]], n_boot: int = 1000,
                seed: int = 0) -> dict[str, dict]:
    """Elo from all games, and a 95% interval from resampling SESSIONS (turns of one session are not independent)."""
    point = bradley_terry([g for gs in games_by_session.values() for g in gs])
    rng, keys = random.Random(seed), list(games_by_session)
    boots = defaultdict(list)
    for _ in range(n_boot if len(keys) > 1 else 0):
        sample = [g for k in rng.choices(keys, k=len(keys)) for g in games_by_session[k]]
        for player, elo in bradley_terry(sample).items():
            boots[player].append(elo)
    out = {}
    for player, elo in point.items():
        b = sorted(boots[player])
        ci = [round(b[int(0.025 * len(b))], 1), round(b[int(0.975 * len(b)) - 1], 1)] if len(b) > 10 else None
        out[player] = {"elo": elo, "ci95": ci}
    return out


# ---------- running over an experiment ----------
def _cached(path: Path, compute, force: bool):
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    return value


def _arm_dirs(exp_dir: Path, session: dict) -> list[Path]:
    root = exp_dir / session["id"]
    return sorted(d for d in root.glob("M*_r*") if (d / "session.json").exists()) if root.exists() else []


def judge_turns(sessions: list[dict], exp_dir: Path, model: str, modes: list[str], llm_factory=LLMClient,
                repeats: int = REPEATS, force: bool = False, log=print) -> dict:
    """rubric and/or scores for every editing turn of every arm; returns per-arm aggregates."""
    tracer = Tracer(exp_dir / "judge_trace", run_id=f"judge_{model}")
    llm = llm_factory(model, tracer)
    per_arm: dict[str, dict] = defaultdict(lambda: {"rubric": [], "scores": []})
    errors = []
    for session in sessions:
        for arm_dir in _arm_dirs(exp_dir, session):
            dirs = _turn_dirs(arm_dir)
            for k in range(2, len(dirs) + 1):
                v = turn_view(session, arm_dir, k)
                arm = arm_dir.name.split("_")[0]
                questions = session["turns"][k - 1].get("rubric", [])
                try:                    # one failed call (the relay drops out) skips this turn, not the whole run
                    if "rubric" in modes and questions:
                        r = _cached(dirs[k - 1] / "judge" / f"rubric__{model}.json",
                                    lambda: majority([ask_rubric(llm, v, questions) for _ in range(repeats)]), force)
                        per_arm[arm]["rubric"].append({"session": session["id"], "turn": k, "answers": r})
                    if "scores" in modes:
                        s = _cached(dirs[k - 1] / "judge" / f"scores__{model}.json",
                                    lambda: mean_std([ask_scores(llm, v) for _ in range(repeats)]), force)
                        per_arm[arm]["scores"].append({"session": session["id"], "turn": k, "scores": s})
                    log(f"judged {session['id']} {arm_dir.name} t{k:02d}")
                except Exception as e:
                    errors.append({"session": session["id"], "arm": arm_dir.name, "turn": k, "error": repr(e)})
                    log(f"!!! {session['id']} {arm_dir.name} t{k:02d}: {e!r} (rerun to retry; finished turns are cached)")
    summary = {arm: summarize(d) for arm, d in sorted(per_arm.items())}
    summary["_judge"] = {"model": model, "repeats": repeats, "cost_usd": tracer.summary()["total"].get("cost_usd"),
                         "errors": errors}
    (exp_dir / f"judge__{model}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


def summarize(d: dict) -> dict:
    """Rubric pass rate for the current request (Q*) and for preservation (P*); mean score per dimension."""
    answers = [(qid, a) for t in d["rubric"] for qid, a in t["answers"].items()]
    rate = lambda xs: round(sum(a["answer"] == "yes" for _, a in xs) / len(xs), 3) if xs else None  # noqa: E731
    out = {"rubric_request": rate([x for x in answers if x[0].startswith("Q")]),
           "rubric_preserved": rate([x for x in answers if x[0].startswith("P")]),
           "rubric_questions": len(answers),
           "rubric_agreement": round(statistics.mean(a["agreement"] for _, a in answers), 3) if answers else None}
    for dim in DIMENSIONS:
        xs = [t["scores"][dim]["mean"] for t in d["scores"] if t["scores"][dim]["mean"] is not None]
        sds = [t["scores"][dim]["std"] for t in d["scores"] if t["scores"][dim]["std"] is not None]
        out[f"score_{dim}"] = round(statistics.mean(xs), 3) if xs else None
        out[f"std_{dim}"] = round(statistics.mean(sds), 3) if sds else None
    return out


def judge_pairs(sessions: list[dict], exp_dir: Path, model: str, arm_a: str, arm_b: str, llm_factory=LLMClient,
                force: bool = False, log=print) -> dict:
    """Same session, same repeat index, same turn: arm_a vs arm_b, swapped. Elo per dimension with session CIs."""
    tracer = Tracer(exp_dir / "judge_trace", run_id=f"pairwise_{model}")
    llm = llm_factory(model, tracer)
    games: dict[str, dict[str, list]] = {d: defaultdict(list) for d in DIMENSIONS}
    verdicts = []
    for session in sessions:
        root = exp_dir / session["id"]
        for a_dir in sorted(root.glob(f"{arm_a}_r*")) if root.exists() else []:
            b_dir = root / a_dir.name.replace(arm_a, arm_b, 1)
            if not (a_dir / "session.json").exists() or not (b_dir / "session.json").exists():
                continue
            n = min(len(_turn_dirs(a_dir)), len(_turn_dirs(b_dir)))
            for k in range(2, n + 1):
                a, b = turn_view(session, a_dir, k, before_views=2), turn_view(session, b_dir, k, before_views=2)
                path = root / "pairwise" / f"{a_dir.name}_vs_{b_dir.name}_t{k:02d}__{model}.json"
                try:
                    v = _cached(path, lambda: compare_swapped(llm, a, b), force)
                except Exception as e:
                    log(f"!!! {session['id']} {a_dir.name} vs {b_dir.name} t{k:02d}: {e!r} (rerun to retry)")
                    continue
                verdicts.append({"session": session["id"], "pair": [a_dir.name, b_dir.name], "turn": k, "verdict": v})
                for d in DIMENSIONS:
                    if v[d] != "invalid":
                        games[d][session["id"]].append((arm_a, arm_b, {"A": 1.0, "B": 0.0, "tie": 0.5}[v[d]]))
                log(f"compared {session['id']} {a_dir.name} vs {b_dir.name} t{k:02d}")
    result = {"model": model, "arms": [arm_a, arm_b], "n_comparisons": len(verdicts),
              "elo": {d: elo_with_ci(dict(games[d])) for d in DIMENSIONS},
              "wins": {d: dict(Counter(x["verdict"][d] for x in verdicts)) for d in DIMENSIONS},
              "cost_usd": tracer.summary()["total"].get("cost_usd"), "verdicts": verdicts}
    (exp_dir / f"pairwise_{arm_a}_vs_{arm_b}__{model}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", required=True)
    ap.add_argument("--model", required=True, help="a GPT or Gemini model name, as the relay calls it")
    ap.add_argument("--modes", nargs="*", choices=["rubric", "scores"], default=["rubric", "scores"])
    ap.add_argument("--pairwise", nargs=2, metavar=("ARM_A", "ARM_B"))
    ap.add_argument("--sessions", nargs="*")
    ap.add_argument("--file", type=Path, default=SESSIONS_FILE)
    ap.add_argument("--repeats", type=int, default=REPEATS)
    ap.add_argument("--force", action="store_true", help="ignore cached verdicts")
    args = ap.parse_args()
    sessions, exp_dir = load_sessions(args.file, args.sessions), Path("runs/sessions") / args.exp
    if args.pairwise:
        r = judge_pairs(sessions, exp_dir, args.model, *args.pairwise, force=args.force)
        print(json.dumps({k: r[k] for k in ("n_comparisons", "elo", "wins", "cost_usd")}, indent=1))
    else:
        print(json.dumps(judge_turns(sessions, exp_dir, args.model, args.modes, repeats=args.repeats,
                                     force=args.force), indent=1))


if __name__ == "__main__":
    main()
