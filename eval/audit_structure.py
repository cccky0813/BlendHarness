"""Structural audit of ALL past builds, without a judge model: re-run each final_script.py in the sandbox,
read the scene with the Scene-Inspector, and check it with analysis/structure.py.

    python eval/audit_structure.py                 # every build under runs/ (snapshots are cached)
    python eval/audit_structure.py --limit 5       # quick try
    python eval/audit_structure.py --rerun         # ignore cached snapshots

Answers, for the spatial-repair project (docs/agentic_rl_plan.md):
  1. how often builds have floating or sunk parts, measured by geometry instead of the Critic;
  2. how often the Critic APPROVED such a build (a miss of the judge);
  3. whether parts can be moved by name later (separate mesh objects, non-default names).
Writes runs/analysis/audit/report.md, rows.jsonl and cases.jsonl (defective builds, for a real-world test set).
No LLM call; costs only Blender time.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from blendharness.analysis import find_runs, load_trajectory
from blendharness.analysis.structure import audit
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.scene import SCENE_INSPECTOR_SCRIPT, SCENE_PREFIX, extract_prefixed_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "analysis" / "audit"

parser = argparse.ArgumentParser()
parser.add_argument("--runs", default=str(ROOT / "runs"))
parser.add_argument("--exclude", nargs="*", default=["smoke"], help="experiment names to skip")
parser.add_argument("--limit", type=int, help="only the first N builds")
parser.add_argument("--rerun", action="store_true", help="ignore cached snapshots")
args = parser.parse_args()

trajectories = [load_trajectory(d, args.runs) for d in find_runs(args.runs)]
trajectories = [t for t in trajectories if t.experiment not in args.exclude and "analysis" not in t.run_dir
                and (Path(t.run_dir) / "final_script.py").exists()]
if args.limit:
    trajectories = trajectories[:args.limit]
(OUT / "snapshots").mkdir(parents=True, exist_ok=True)


def snapshot_path(t) -> Path:
    rel = Path(t.run_dir).resolve().relative_to(Path(args.runs).resolve())
    return OUT / "snapshots" / ("__".join(rel.parts) + ".json")


# ---------- 1. snapshots (resumable: one cached file per build) ----------
todo = [t for t in trajectories if args.rerun or not snapshot_path(t).exists()]
print(f"{len(trajectories)} builds, {len(trajectories) - len(todo)} cached, {len(todo)} to run in Blender")
if todo:
    with BlenderSession(OUT / "blender.log") as session:
        for n, t in enumerate(todo, 1):
            script = (Path(t.run_dir) / "final_script.py").read_text(encoding="utf-8")
            if not script.strip():
                snap = {"error": "empty final_script.py (no working script)"}
            else:
                r = session.execute(script + "\n" + SCENE_INSPECTOR_SCRIPT, reset=True, timeout_s=180)
                snap = (extract_prefixed_json(r.output, SCENE_PREFIX) or {"error": "Scene-Inspector returned nothing"}) \
                    if r.ok else {"error": r.error[:300]}
            snapshot_path(t).write_text(json.dumps(snap), encoding="utf-8")
            print(f"  [{n}/{len(todo)}] {t.experiment}/{t.variant}/{t.task_id}: "
                  f"{snap.get('error') or str(snap['summary']['mesh_object_count']) + ' meshes'}")

# ---------- 2. audit ----------
rows = []
for t in trajectories:
    snap = json.loads(snapshot_path(t).read_text(encoding="utf-8"))
    a = {"meshes": 0, "defect": False, "error": snap["error"]} if "error" in snap else audit(snap)
    rows.append({"run_dir": t.run_dir, "experiment": t.experiment, "variant": t.variant, "task": t.task_id,
                 "prompt": t.prompt, "approved": t.approved, **a})
with (OUT / "rows.jsonl").open("w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
with (OUT / "cases.jsonl").open("w", encoding="utf-8") as f:
    for r in rows:
        if r["defect"]:
            f.write(json.dumps({k: r[k] for k in ("run_dir", "task", "prompt", "approved", "floating_parts",
                                                   "sunk_parts", "meshes")}, ensure_ascii=False) + "\n")


# ---------- 3. report ----------
def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "-"


ok = [r for r in rows if not r.get("error")]
multi = [r for r in ok if r["meshes"] >= 2]
approved, rejected = [r for r in ok if r["approved"]], [r for r in ok if not r["approved"]]
defect = lambda rs: sum(r["defect"] for r in rs)  # noqa: E731
floating = lambda rs: sum(r["floating_components"] > 0 for r in rs)  # noqa: E731
sunk = lambda rs: sum(bool(r["sunk_parts"]) for r in rs)  # noqa: E731

lines = [f"# Structural audit ({time.strftime('%Y-%m-%d')})", "",
         f"{len(rows)} builds with a final script (excluded: {', '.join(args.exclude) or 'none'}); "
         f"{len(rows) - len(ok)} could not be re-run or had no mesh. Geometry only (axis-aligned boxes, "
         "2% relative tolerances as in the skill admission gate), no judge model.", "",
         "## Defects (floating = a group of touching parts that does not reach the ground)", "",
         "| | All | Approved by the Critic | Not approved |", "|---|---|---|---|",
         f"| any defect | {pct(defect(ok), len(ok))} | {pct(defect(approved), len(approved))} | {pct(defect(rejected), len(rejected))} |",
         f"| floating parts | {pct(floating(ok), len(ok))} | {pct(floating(approved), len(approved))} | {pct(floating(rejected), len(rejected))} |",
         f"| sunk below ground | {pct(sunk(ok), len(ok))} | {pct(sunk(approved), len(approved))} | {pct(sunk(rejected), len(rejected))} |",
         f"| whole model elevated (e.g. wall-mounted, not counted as a defect) | {pct(sum(r['elevated'] for r in ok), len(ok))} | | |",
         "", "The 'approved' column is the Critic's miss rate: builds it passed that still have a geometric defect.", "",
         "## Can a repair step move parts by name?", "",
         "| Metric | Value |", "|---|---|",
         f"| builds with at least 2 separate mesh objects | {pct(len(multi), len(ok))} |",
         f"| mesh objects per build (median / max) | {statistics.median([r['meshes'] for r in ok]) if ok else '-'} / "
         f"{max((r['meshes'] for r in ok), default='-')} |",
         f"| mesh objects with a default name (Cube.003 ...) | {pct(sum(r['default_named'] for r in ok), sum(r['meshes'] for r in ok))} |",
         f"| builds with parented meshes | {pct(sum(r['parented'] > 0 for r in ok), len(ok))} |",
         f"| builds with heavily overlapping boxes (informational) | {pct(sum(r['heavy_overlaps'] > 0 for r in ok), len(ok))} |",
         "", "## By source", "", "| Source | Builds | Defect | Floating | Sunk | Approved with defect |", "|---|---|---|---|---|---|"]
for name in sorted({(r["experiment"], r["variant"]) for r in ok}):
    g = [r for r in ok if (r["experiment"], r["variant"]) == name]
    lines.append(f"| {name[0]}/{name[1]} | {len(g)} | {defect(g)} | {floating(g)} | {sunk(g)} | "
                 f"{sum(r['defect'] and r['approved'] for r in g)} |")
lines += ["", "## Defective builds", "",
          "Gap = distance from a floating group to the nearest other part: a few cm means a detached part, "
          "about a metre usually means a wall-mounted item in a scene without a wall object.", "",
          "| Build | Approved | Floating parts | Gap (m) | Sunk parts |", "|---|---|---|---|---|"]
for r in sorted((r for r in ok if r["defect"]), key=lambda r: (not r["approved"], r["task"])):
    lines.append(f"| {r['experiment']}/{r['variant']}/{r['task']} | {'yes' if r['approved'] else 'no'} | "
                 f"{', '.join(r['floating_parts'][:6])} | {', '.join(map(str, r['floating_gaps']))} | "
                 f"{', '.join(r['sunk_parts'][:4])} |")
errors = [r for r in rows if r.get("error")]
if errors:
    lines += ["", "## Not audited", ""] + [f"- {r['experiment']}/{r['variant']}/{r['task']}: {r['error'][:150]}" for r in errors]

(OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:30]))
print(f"\n... full report: {OUT / 'report.md'}")
