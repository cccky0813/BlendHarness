"""Trajectory analysis across ALL past builds: process metrics and the failure taxonomy.

    python eval/analyze.py                       # every build under runs/, issues labelled by Haiku (cached)
    python eval/analyze.py --no-llm              # keyword labels only, no API call
    python eval/analyze.py --exclude smoke       # skip experiments by name

Writes runs/analysis/report.md and runs/analysis/trajectories.jsonl (one row per build).
"""

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from blendharness.analysis import (NON_SPATIAL, SPATIAL, IssueClassifier, aggregate, find_runs, load_trajectory,
                                   repair_table, run_metrics, spatial_share)
from blendharness.core.llm import LLMClient
from blendharness.core.tracing import Tracer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "analysis"

parser = argparse.ArgumentParser()
parser.add_argument("--runs", default=str(ROOT / "runs"))
parser.add_argument("--exclude", nargs="*", default=["smoke"], help="experiment names to skip")
parser.add_argument("--no-llm", action="store_true", help="keyword labels only")
parser.add_argument("--model", default="claude-haiku-4-5")
args = parser.parse_args()

trajectories = [load_trajectory(d, args.runs) for d in find_runs(args.runs)]
trajectories = [t for t in trajectories if t.experiment not in args.exclude and "analysis" not in t.run_dir]

ask = None
if not args.no_llm:
    llm = LLMClient(args.model, Tracer(OUT / "labeling", run_id=time.strftime("label_%Y%m%d_%H%M%S")))
    ask = lambda prompt: llm.chat([{"role": "user", "content": prompt}], agent="labeler").text  # noqa: E731
classifier = IssueClassifier(OUT / "issue_labels.json", ask=ask)
issues = [i for t in trajectories for i in t.issues]
for issue, label in zip(issues, classifier.classify([i.text for i in issues])):
    issue.category = label

summary = aggregate(trajectories)
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "trajectories.jsonl").open("w", encoding="utf-8") as f:
    for t in trajectories:
        f.write(json.dumps({"run_dir": t.run_dir, "experiment": t.experiment, "variant": t.variant, "task": t.task_id,
                            **{k: v for k, v in run_metrics(t).items()},
                            "issues": [asdict(i) for i in t.issues]}, ensure_ascii=False) + "\n")


# ---------- report ----------
def pct(x):
    return "-" if x is None or x != x else f"{100 * x:.0f}%"


def money(x):
    return "-" if x is None or x != x else f"${x:.3f}"


lines = [f"# Trajectory analysis ({time.strftime('%Y-%m-%d')})", "",
         f"{summary['builds']} builds from {len({t.experiment for t in trajectories})} sources "
         f"(excluded: {', '.join(args.exclude) or 'none'}); issue labels: {'keywords' if args.no_llm else args.model}.", ""]

o, e, p = summary["outcome"], summary["efficiency"], summary["process"]
lines += ["## Overview", "", "| Metric | Value |", "|---|---|",
          f"| pass rate (final) | {pct(o['pass_rate'])} |",
          f"| pass in round 0 (no revision needed) | {pct(o['pass_round0'])} |",
          f"| mean rounds | {o['mean_rounds']:.2f} |",
          f"| cost per build (mean / median) | {money(e['cost_per_build'])} / {money(e['median_cost'])} |",
          f"| round 0 share of coder cost | {pct(e['round0_share_of_coder_cost'])} |",
          f"| hidden thinking share of coder output (est., all / revisions) | {pct(e['hidden_share_of_output'])} / {pct(e['hidden_share_revisions'])} |",
          f"| mean coder steps per build | {e['mean_coder_steps']:.1f} |",
          f"| code execution failure rate | {pct(p['exec_failure_rate'])} |",
          f"| recovery after a failed execution (same coder run) | {pct(p['recovery_rate'])} |",
          f"| coder run status | {p['coder_run_status']} |",
          f"| builds with a rail intervention | {p['builds_with_rail_intervention']} |",
          f"| view_script calls / guard blocks | {p['view_script_calls']} / {p['guard_blocks']} |", ""]

lines += ["## Failure taxonomy (all Critic issues + regressions)", "",
          "raised = times found; revised = had at least one revision round; fixed = last verdict FIXED; "
          "open at failure = still unfixed in a build that ended unapproved.", "",
          "| Category | Type | Raised | Revised | Fixed | Fix rate | Open at failure |", "|---|---|---|---|---|---|---|"]
for cat, r in summary["repair"].items():
    kind = "spatial" if cat in SPATIAL else ("non-spatial" if cat in NON_SPATIAL else "")
    lines.append(f"| {cat} | {kind} | {r['raised']} | {r['revised']} | {r['fixed']} | {pct(r['fix_rate'])} | {r['open_at_failure']} |")
lines += ["", f"**Spatial share**: {pct(spatial_share(summary['repair'], 'raised'))} of raised issues, "
              f"{pct(spatial_share(summary['repair'], 'open_at_failure'))} of issues open in failed builds.", ""]

lines += ["## By source", "", "| Source | Builds | Pass | Pass r0 | Cost/build | Spatial share |", "|---|---|---|---|---|---|"]
for name in sorted({(t.experiment, t.variant) for t in trajectories}):
    group = [t for t in trajectories if (t.experiment, t.variant) == name]
    s = aggregate(group)
    lines.append(f"| {name[0]}/{name[1]} | {s['builds']} | {pct(s['outcome']['pass_rate'])} | "
                 f"{pct(s['outcome']['pass_round0'])} | {money(s['efficiency']['cost_per_build'])} | "
                 f"{pct(spatial_share(repair_table(group)))} |")

lines += ["", "## Examples per category", ""]
for cat in summary["repair"]:
    examples = [i.text for i in issues if i.category == cat][:3]
    lines += [f"**{cat}**", *[f"- {x[:220]}" for x in examples], ""]

(OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[:40]))
print(f"\n... full report: {OUT / 'report.md'}")
