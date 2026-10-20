"""Batch evaluation: run every task x variant x repeat through blendharness/workflow/build.py.

- Resumable: a job whose result.json exists is skipped, so an interrupted experiment just restarts.
- Isolated: one job crashing (API outage, bug) is recorded in crash.json and does not stop the others;
  crashed jobs have no result.json, so they are retried on the next start, continuing from their
  latest checkpoint (phases already done, e.g. round 0, are not paid for twice).
- Fair ordering: jobs are interleaved (repeat -> task -> variant) so variants run under similar API
  conditions instead of one variant first and the other an hour later.

Usage:
  python eval/run_eval.py --exp m3_vs_baseline --variants baseline m3 --repeats 2 --workers 2
  python eval/run_eval.py --exp m3_vs_baseline --dry-run        # list jobs and what is already done
Then: python eval/report.py --exp m3_vs_baseline
"""

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

from blendharness.workflow.build import BuildConfig, run_build

ROOT = Path(__file__).resolve().parents[1]

VARIANTS = {
    "baseline": BuildConfig(edit_tools=False, compact_context=False),  # before M3 (full rewrites, full history)
    "m3": BuildConfig(),                                               # edit_script + history compaction
    # M4 learning curve: skills_dir is filled in per experiment (runs/eval/<exp>/skills)
    "train": BuildConfig(learn_skills=True),                          # build + extract/admit skills (run sequentially!)
    "noskill": BuildConfig(),                                          # test without the library
    "skill": BuildConfig(track_skill_usage=False),                     # test with the FROZEN trained library
    # RSI stop C: lessons learned from past failures (memory/lessons), one revision round, frozen library
    "nolessons": BuildConfig(rounds=1),
    "lessons": BuildConfig(rounds=1),                                  # lessons_dir: the experiment's snapshot
}
SKILL_VARIANTS = {"train", "skill"}
LESSON_VARIANTS = {"lessons"}

parser = argparse.ArgumentParser()
parser.add_argument("--exp", required=True, help="experiment name -> runs/eval/<exp>/")
parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
parser.add_argument("--tasks", nargs="*", help="task ids (default: all in the task file)")
parser.add_argument("--task-file", default="eval/tasks.json")
parser.add_argument("--split", choices=["train", "test"], help="only tasks with this split")
parser.add_argument("--repeats", type=int, default=2)
parser.add_argument("--workers", type=int, default=2, help="parallel jobs (each runs its own Blender)")
parser.add_argument("--coder-model", help="override coder model for all variants")
parser.add_argument("--critic-model", help="override critic/verifier model for all variants")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

tasks = json.loads((ROOT / args.task_file).read_text(encoding="utf-8"))
if args.split:
    tasks = [t for t in tasks if t.get("split") == args.split]
if args.tasks:
    tasks = [t for t in tasks if t["id"] in args.tasks]
exp_dir = ROOT / "runs" / "eval" / args.exp


def config_for(variant: str) -> BuildConfig:
    cfg = VARIANTS[variant]
    if args.coder_model:
        cfg = replace(cfg, coder_model=args.coder_model)
    if args.critic_model:
        cfg = replace(cfg, critic_model=args.critic_model)
    if variant in SKILL_VARIANTS:
        cfg = replace(cfg, skills_dir=str(exp_dir / "skills"))
    if variant in LESSON_VARIANTS:
        cfg = replace(cfg, lessons_dir=str(exp_dir / "lessons"))
    return cfg


task_by_id = {t["id"]: t for t in tasks}
jobs = [(rep, t["id"], variant) for rep in range(1, args.repeats + 1) for t in tasks for variant in args.variants]
job_dir = {job: exp_dir / job[2] / f"{job[1]}_r{job[0]}" for job in jobs}
todo = [j for j in jobs if not (job_dir[j] / "result.json").exists()]
print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} already done, {len(todo)} to run -> {exp_dir}")
if args.dry_run:
    for j in jobs:
        print(f"  {'done' if j not in todo else 'todo'}  {j[2]:<9} {j[1]:<11} r{j[0]}")
    sys.exit(0)

if "skill" in args.variants and not any((exp_dir / "skills").glob("*/skill.py")):
    sys.exit(f"variant 'skill' needs a trained library in {exp_dir / 'skills'}: run the 'train' variant first")
if "train" in args.variants and args.workers != 1:
    print("note: 'train' accumulates a library, forcing --workers 1 (sequential, fixed order)")
    args.workers = 1
exp_dir.mkdir(parents=True, exist_ok=True)
if LESSON_VARIANTS & set(args.variants) and not (exp_dir / "lessons" / "lessons.json").exists():
    # Snapshot the library once: every job of this experiment sees the same lessons, even if lessons/ changes.
    (exp_dir / "lessons").mkdir(exist_ok=True)
    shutil.copy(ROOT / "lessons" / "lessons.json", exp_dir / "lessons" / "lessons.json")
lessons_now = [x["id"] for x in json.loads((exp_dir / "lessons" / "lessons.json").read_text())] \
    if (exp_dir / "lessons" / "lessons.json").exists() else []
commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
skills_now = sorted(p.parent.name for p in (exp_dir / "skills").glob("*/skill.py"))
manifest = {"exp": args.exp, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "git_commit": commit,
            "task_file": args.task_file, "split": args.split, "skills_at_start": skills_now, "lessons_at_start": lessons_now,
            "repeats": args.repeats, "tasks": [t["id"] for t in tasks],
            "variants": {v: asdict(config_for(v)) for v in args.variants}}
(exp_dir / f"manifest_{'_'.join(args.variants)}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

print_lock = threading.Lock()


def run_job(job) -> str:
    rep, task_id, variant = job
    task = task_by_id[task_id]
    tag = f"[{variant}/{task_id}/r{rep}]"
    out = job_dir[job]
    out.mkdir(parents=True, exist_ok=True)
    (out / "crash.json").unlink(missing_ok=True)

    def log(msg: str) -> None:
        with print_lock:
            print(f"{tag} {msg}", flush=True)

    t0 = time.perf_counter()
    try:
        rec = run_build(task["prompt"], config_for(variant), out, log=log, resume=True)
        return (f"{tag} {rec['status']} | rounds={rec['n_rounds']} | ${rec['llm_total']['cost_usd']:.3f} | "
                f"{rec['wall_time_s']:.0f}s")
    except Exception as exc:  # isolate: one failing job must not kill the experiment
        (out / "crash.json").write_text(json.dumps(
            {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(),
             "after_s": round(time.perf_counter() - t0, 1)}, indent=2), encoding="utf-8")
        return f"{tag} CRASHED: {type(exc).__name__}: {str(exc)[:150]}"


t_start = time.perf_counter()
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futures = [pool.submit(run_job, j) for j in todo]
    for i, fut in enumerate(as_completed(futures), 1):
        with print_lock:
            print(f"=== [{i}/{len(todo)}] {fut.result()}", flush=True)
print(f"\nfinished in {(time.perf_counter() - t_start) / 60:.1f} min. Report: python eval/report.py --exp {args.exp}")
