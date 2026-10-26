"""Post-fix baseline: what goes wrong now, per task set and coder (runs/eval/postfix).

Run after the experiment and after the three cached analyses it joins:
    python eval/analyze.py                 # issue labels (Haiku, cached)
    python eval/audit_structure.py         # floating / sunk on final scripts
    python eval/validate_relations.py      # round-0 snapshots + relation flags (screening only)
    python eval/postfix_report.py

Round-0 numbers are comparable with the historical runs of the same tasks (same prompts, same first build);
final numbers are not (the historical lessons runs allowed one revision round, postfix allows two).
Writes runs/eval/postfix/report_postfix.md.
"""

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from blendharness.analysis import SPATIAL, IssueClassifier, find_runs, load_trajectory

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
EXP = RUNS / "eval" / "postfix"
TASKS = {t["id"]: t["set"] for t in json.loads((ROOT / "eval" / "tasks_postfix.json").read_text())}
HISTORY = {"old_m3": ("m3_vs_baseline", "m3"), "old_lessons": ("lessons_confirm", "nolessons")}


def label(trajectories):
    issues = [i for t in trajectories for i in t.issues]
    for issue, cat in zip(issues, IssueClassifier(RUNS / "analysis" / "issue_labels.json").classify([i.text for i in issues])):
        issue.category = cat


def audit_rows() -> dict[str, dict]:
    path = RUNS / "analysis" / "audit" / "rows.jsonl"
    return {json.loads(l)["run_dir"]: json.loads(l) for l in path.read_text().splitlines()} if path.exists() else {}


def summarize(ts, audit) -> dict:
    r0 = [i for t in ts for i in t.issues if i.raised_in == 0 and not i.regression]
    cats = Counter(i.category for i in r0)
    audited = [audit[t.run_dir] for t in ts if t.run_dir in audit and not audit[t.run_dir].get("error")]
    return {"n": len(ts), "pass_r0": statistics.mean(t.approved_in_round0 for t in ts) if ts else float("nan"),
            "pass": statistics.mean(t.approved for t in ts) if ts else float("nan"),
            "issues_r0": len(r0) / len(ts) if ts else float("nan"),
            "spatial_r0": sum(cats[c] for c in SPATIAL) / len(ts) if ts else float("nan"),
            "cats": cats, "cost": statistics.mean(t.cost_usd for t in ts) if ts else float("nan"),
            "defect": sum(a["defect"] for a in audited), "audited": len(audited)}


def fmt(s: dict) -> str:
    return (f"| {s['n']} | {s['pass_r0']:.0%} | {s['pass']:.0%} | {s['issues_r0']:.2f} | {s['spatial_r0']:.2f} | "
            f"{s['defect']}/{s['audited']} | ${s['cost']:.3f} |")


post = [load_trajectory(d, RUNS) for d in find_runs(EXP)]
label(post)
audit = audit_rows()
groups = defaultdict(list)
for t in post:
    groups[(TASKS.get(t.task_id, "?"), t.variant)].append(t)

lines = ["# Post-fix baseline (runs/eval/postfix)", "",
         "Round 0 = the first build and the Critic's first review. Spatial = floating + interpenetration + "
         "placement + proportion. Structural defects = floating or sunk parts in the final scene (geometry only).", "",
         "| Set / coder | Builds | Pass r0 | Final pass | Issues r0 / build | Spatial r0 / build | Structural defects | Cost / build |",
         "|---|---|---|---|---|---|---|---|"]
for key in sorted(groups):
    lines.append(f"| {key[0]} / {key[1]} " + fmt(summarize(groups[key], audit)))

lines += ["", "## Same tasks before the fixes (round 0 only is comparable)", "",
          "| Set | Source | Builds | Pass r0 | Spatial r0 / build | Issues r0 / build |", "|---|---|---|---|---|---|"]
for set_name, (exp, variant) in HISTORY.items():
    hist = [load_trajectory(d, RUNS) for d in find_runs(RUNS / "eval" / exp / variant)]
    label(hist)
    for name, ts in (("before (" + exp + "/" + variant + ")", hist), ("after (postfix/m3)", groups.get((set_name, "m3"), []))):
        s = summarize(ts, {})
        lines.append(f"| {set_name} | {name} | {s['n']} | {s['pass_r0']:.0%} | {s['spatial_r0']:.2f} | {s['issues_r0']:.2f} |")

lines += ["", "## Round-0 issue categories", "", "| Set / coder | " + " | ".join(
    ["floating", "interpenetration", "placement", "proportion", "missing", "material", "shape", "other"]) + " |",
    "|---|" + "---|" * 8]
for key in sorted(groups):
    c = summarize(groups[key], audit)["cats"]
    lines.append(f"| {key[0]} / {key[1]} | " + " | ".join(str(c.get(k, 0)) for k in
                 ["floating", "interpenetration", "placement", "proportion", "missing", "material", "shape", "other"]) + " |")

lines += ["", "## Spatial issues in round 0 (for manual review)", ""]
for key in sorted(groups):
    for t in groups[key]:
        for i in t.issues:
            if i.raised_in == 0 and not i.regression and i.category in SPATIAL:
                lines.append(f"- [{key[0]}/{key[1]}] **{t.task_id}** ({i.category}, {'fixed' if i.fixed else 'not fixed'}): {i.text[:220]}")

(EXP / "report_postfix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:60]))
