"""Does the relation checker (analysis/relations.py) see what the Critic sees? Validate it before using it.

    python eval/validate_relations.py             # round-0 scenes of every build that has a round-0 checkpoint
    python eval/validate_relations.py --rerun     # ignore cached snapshots

For each build: re-run the round-0 script (from checkpoints/000_round0.json) in the sandbox, run the checker,
and compare with the issues the Critic raised in round 0 (labels from the eval/analyze.py cache, no LLM call).
A finding is "named by the Critic" when a spatial Critic issue mentions one of its part names (word match);
a relational Critic issue is "caught" when a finding names one of the parts it mentions.

Neither side is ground truth: the Critic misses defects (see the structural audit) and the checker only knows
bounding boxes and names. Disagreements are listed with their renders for a human to look at.
Split: dev = lessons_ab (thresholds may be tuned on it), test = every other experiment (report only).
Writes runs/analysis/relations/report.md and findings.jsonl.
"""

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from blendharness.analysis import IssueClassifier, find_runs, load_trajectory
from blendharness.analysis.relations import check_relations
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.scene import SCENE_INSPECTOR_SCRIPT, SCENE_PREFIX, extract_prefixed_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "analysis" / "relations"
DEV = {"lessons_ab"}
SPATIAL = {"placement", "proportion", "floating", "interpenetration"}
RELATIONAL = {"placement", "proportion"}
GENERIC = {"left", "right", "front", "back", "upper", "lower", "top", "bottom", "inner", "outer", "body", "main",
           "base", "part", "mesh", "north", "south", "east", "west"}

parser = argparse.ArgumentParser()
parser.add_argument("--runs", default=str(ROOT / "runs"))
parser.add_argument("--rerun", action="store_true")
args = parser.parse_args()
runs_root = Path(args.runs)


def round0_script(run_dir: Path) -> str | None:
    cps = sorted((run_dir / "checkpoints").glob("[0-9][0-9][0-9]_round0.json"))
    return json.loads(cps[0].read_text(encoding="utf-8"))["state"]["current_script"] if cps else None


def name_tokens(name: str) -> set[str]:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).replace("_", " ").replace(".", " ").lower().split()
    return {w.rstrip("s") for w in words if len(w) > 2 and not w.isdigit() and w not in GENERIC}


def mentions(text: str, tokens: set[str]) -> bool:
    words = {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower())}
    return bool(tokens & words)


trajectories = [load_trajectory(d, runs_root) for d in find_runs(runs_root)]
trajectories = [t for t in trajectories if t.experiment != "smoke" and round0_script(Path(t.run_dir))]
issues = [i for t in trajectories for i in t.issues]
for issue, label in zip(issues, IssueClassifier(runs_root / "analysis" / "issue_labels.json").classify([i.text for i in issues])):
    issue.category = label
(OUT / "snapshots").mkdir(parents=True, exist_ok=True)


def snap_path(t) -> Path:
    rel = Path(t.run_dir).resolve().relative_to(runs_root.resolve())
    return OUT / "snapshots" / ("__".join(rel.parts) + "__round0.json")


todo = [t for t in trajectories if args.rerun or not snap_path(t).exists()]
print(f"{len(trajectories)} builds with a round-0 checkpoint, {len(todo)} to run in Blender")
if todo:
    with BlenderSession(OUT / "blender.log") as session:
        for t in todo:
            r = session.execute(round0_script(Path(t.run_dir)) + "\n" + SCENE_INSPECTOR_SCRIPT, reset=True, timeout_s=180)
            snap = (extract_prefixed_json(r.output, SCENE_PREFIX) or {"error": "no output"}) if r.ok else {"error": r.error[:300]}
            snap_path(t).write_text(json.dumps(snap), encoding="utf-8")

rows, findings_out = [], []
for t in trajectories:
    snap = json.loads(snap_path(t).read_text(encoding="utf-8"))
    if "error" in snap:
        continue
    findings = check_relations(snap)["findings"]
    critic = [i for i in t.issues if i.raised_in == 0 and not i.regression]
    spatial = [i for i in critic if i.category in SPATIAL]
    for f in findings:
        tokens = set().union(*(name_tokens(p) for p in f["parts"]))
        f["named_by_critic"] = any(mentions(i.text, tokens) for i in spatial)
    caught = [any(mentions(i.text, set().union(*(name_tokens(p) for p in f["parts"]))) for f in findings)
              for i in critic if i.category in RELATIONAL]
    result = json.loads((Path(t.run_dir) / "result.json").read_text(encoding="utf-8"))
    renders = ((result.get("rounds") or [{}])[0].get("renders") or [])
    rows.append({"split": "dev" if t.experiment in DEV else "test", "build": f"{t.experiment}/{t.variant}/{t.task_id}",
                 "run_dir": t.run_dir, "findings": findings, "relational_issues": [i.text for i in critic if i.category in RELATIONAL],
                 "caught": caught, "render": renders[0] if renders else ""})
    for f in findings:
        findings_out.append({"build": rows[-1]["build"], "split": rows[-1]["split"], "render": rows[-1]["render"], **f})
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "findings.jsonl").open("w", encoding="utf-8") as fh:
    for f in findings_out:
        fh.write(json.dumps(f, ensure_ascii=False) + "\n")


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"


lines = [f"# Relation checker vs Critic, round 0 ({time.strftime('%Y-%m-%d')})", "",
         f"{len(rows)} builds with a round-0 checkpoint; dev = {', '.join(sorted(DEV))}, test = the rest.", ""]
for split in ("dev", "test"):
    rs = [r for r in rows if r["split"] == split]
    fs = [f for r in rs for f in r["findings"]]
    rel = [c for r in rs for c in r["caught"]]
    by = defaultdict(list)
    for f in fs:
        by[f["check"]].append(f)
    lines += [f"## {split} ({len(rs)} builds)", "",
              f"- builds with at least one finding: {pct(sum(bool(r['findings']) for r in rs), len(rs))}",
              f"- builds with a relational Critic issue in round 0: {pct(sum(bool(r['relational_issues']) for r in rs), len(rs))}",
              f"- relational Critic issues caught by a finding: {pct(sum(rel), len(rel))}", "",
              "| Check | Findings | Builds | Named by a spatial Critic issue |", "|---|---|---|---|"]
    for check, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        builds = sum(any(x["check"] == check for x in r["findings"]) for r in rs)
        lines.append(f"| {check} | {len(items)} | {builds} | {pct(sum(f['named_by_critic'] for f in items), len(items))} |")
    lines.append("")
lines += ["## Relational Critic issues the checker missed (dev + test)", ""]
for r in rows:
    for text, hit in zip(r["relational_issues"], r["caught"]):
        if not hit:
            lines.append(f"- **{r['build']}**: {text[:200]}")
(OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
counts = Counter(f["check"] for f in findings_out)
print("\nfindings per check:", dict(counts))
