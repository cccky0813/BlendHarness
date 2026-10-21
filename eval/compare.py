"""Paired A/B comparison of two variants of one experiment, with bootstrap confidence intervals.

    python eval/compare.py --exp lessons_ab --a nolessons --b lessons

Pre-registered for the lessons experiment (RSI stop C), decided BEFORE looking at results:
  primary    relational issues (placement + proportion) raised by the Critic in round 0, per build
  secondary  pass in round 0, final pass, cost per build, coder steps
  treatment  did the lessons actually reach the Coder (lessons_used, and in the system prompt it saw)

Pairing is by task: per task, mean(B) - mean(A) over its repeats; the effect is the mean of those
per-task differences, and its 95% CI comes from resampling TASKS (the unit we want to generalize over).
"""

import argparse
import json
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from blendharness.analysis import IssueClassifier, find_runs, load_trajectory, run_metrics
from blendharness.core.llm import LLMClient
from blendharness.core.tracing import Tracer

ROOT = Path(__file__).resolve().parents[1]
RELATIONAL = {"placement", "proportion"}

parser = argparse.ArgumentParser()
parser.add_argument("--exp", required=True)
parser.add_argument("--a", required=True, help="control variant")
parser.add_argument("--b", required=True, help="treatment variant")
parser.add_argument("--no-llm", action="store_true", help="label new issues with keywords only")
parser.add_argument("--bootstrap", type=int, default=10000)
args = parser.parse_args()

exp_dir = ROOT / "runs" / "eval" / args.exp
trajectories = [load_trajectory(d, ROOT / "runs") for d in find_runs(exp_dir)]
trajectories = [t for t in trajectories if t.variant in (args.a, args.b)]

ask = None
if not args.no_llm:
    llm = LLMClient("claude-haiku-4-5", Tracer(ROOT / "runs" / "analysis" / "labeling",
                                               run_id=time.strftime("label_%Y%m%d_%H%M%S")))
    ask = lambda prompt: llm.chat([{"role": "user", "content": prompt}], agent="labeler").text  # noqa: E731
issues = [i for t in trajectories for i in t.issues]
for issue, label in zip(issues, IssueClassifier(ROOT / "runs" / "analysis" / "issue_labels.json", ask=ask)
                        .classify([i.text for i in issues])):
    issue.category = label


def treatment_reached_coder(t) -> bool:
    """lessons_used is recorded AND the first lesson's text is in the system prompt the Coder actually got."""
    result = json.loads((Path(t.run_dir) / "result.json").read_text())
    used = result.get("lessons_used") or []
    if not used:
        return False
    library = exp_dir / ("lessons" if t.variant == "lessons" else f"{t.variant}_library") / "lessons.json"
    lessons = {x["id"]: x["text"] for x in json.loads(library.read_text())}
    for payload in sorted((Path(t.run_dir) / "payloads").glob("*.json")):
        messages = json.loads(payload.read_text()).get("messages") or []
        if messages and messages[0].get("role") == "system" and "Lessons learned" in str(messages[0].get("content")):
            return lessons.get(used[0], "\0") in messages[0]["content"]
    return False


def row(t) -> dict:
    m = run_metrics(t)
    round0 = [i for i in t.issues if i.raised_in == 0 and not i.regression]
    return {"task": t.task_id, "variant": t.variant,
            "relational_r0": sum(i.category in RELATIONAL for i in round0),
            "issues_r0": len(round0),
            "pass_r0": float(t.approved_in_round0), "pass": float(t.approved),
            "cost": t.cost_usd, "steps": m["coder_steps"]}


rows = [row(t) for t in trajectories]
METRICS = [("relational_r0", "relational issues in round 0 (primary, lower is better)"),
           ("issues_r0", "all issues in round 0"), ("pass_r0", "pass in round 0"), ("pass", "final pass"),
           ("cost", "cost per build ($)"), ("steps", "coder steps per build")]


def paired_effect(metric: str) -> tuple[float, float, float, dict]:
    by_task = defaultdict(lambda: {args.a: [], args.b: []})
    for r in rows:
        by_task[r["task"]][r["variant"]].append(r[metric])
    diffs = {task: statistics.mean(v[args.b]) - statistics.mean(v[args.a])
             for task, v in by_task.items() if v[args.a] and v[args.b]}
    values = list(diffs.values())
    rng = random.Random(0)
    boot = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(args.bootstrap))
    return statistics.mean(values), boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1], diffs


def mean_of(variant: str, metric: str) -> float:
    xs = [r[metric] for r in rows if r["variant"] == variant]
    return statistics.mean(xs) if xs else float("nan")


treated = [t for t in trajectories if t.variant == args.b]
reached = sum(treatment_reached_coder(t) for t in treated)
lines = [f"# A/B: {args.a} vs {args.b} ({args.exp}, {time.strftime('%Y-%m-%d')})", "",
         f"{sum(r['variant'] == args.a for r in rows)} vs {sum(r['variant'] == args.b for r in rows)} builds over "
         f"{len({r['task'] for r in rows})} tasks. Treatment reached the Coder in {reached}/{len(treated)} "
         f"'{args.b}' builds.", "",
         f"| Metric | {args.a} | {args.b} | paired diff (B - A) | 95% CI (bootstrap over tasks) |", "|---|---|---|---|---|"]
details = {}
for metric, label in METRICS:
    effect, lo, hi, diffs = paired_effect(metric)
    details[metric] = diffs
    fmt = (lambda x: f"{x:.3f}") if metric == "cost" else (lambda x: f"{x:.2f}")
    lines.append(f"| {label} | {fmt(mean_of(args.a, metric))} | {fmt(mean_of(args.b, metric))} | "
                 f"{effect:+.3f} | [{lo:+.3f}, {hi:+.3f}] |")
lines += ["", "## Per task: relational issues in round 0 (mean over repeats)", "",
          f"| Task | {args.a} | {args.b} | diff |", "|---|---|---|---|"]
for task in sorted({r["task"] for r in rows}):
    a = [r["relational_r0"] for r in rows if r["task"] == task and r["variant"] == args.a]
    b = [r["relational_r0"] for r in rows if r["task"] == task and r["variant"] == args.b]
    if a and b:
        lines.append(f"| {task} | {statistics.mean(a):.1f} | {statistics.mean(b):.1f} | "
                     f"{statistics.mean(b) - statistics.mean(a):+.1f} |")

lines += ["", "## Issue categories in round 0", "", f"| Category | {args.a} | {args.b} |", "|---|---|---|"]
counts = defaultdict(lambda: {args.a: 0, args.b: 0})
for t in trajectories:
    for i in t.issues:
        if i.raised_in == 0 and not i.regression:
            counts[i.category][t.variant] += 1
for cat, c in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
    lines.append(f"| {cat} | {c[args.a]} | {c[args.b]} |")

out = exp_dir / f"compare_{args.a}_vs_{args.b}.md"
out.write_text("\n".join(lines), encoding="utf-8")
(exp_dir / f"compare_{args.a}_vs_{args.b}.json").write_text(json.dumps({"rows": rows, "per_task_diffs": details}, indent=1))
print("\n".join(lines))
