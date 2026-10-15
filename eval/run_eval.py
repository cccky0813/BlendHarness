"""Batch evaluation: run every task x variant x repeat through pipeline/build.py.

- Resumable: a job whose result.json exists is skipped, so an interrupted experiment just restarts.
- Isolated: one job crashing (API outage, bug) is recorded in crash.json and does not stop the others;
  crashed jobs have no result.json, so they are retried on the next start.
- Fair ordering: jobs are interleaved (repeat -> task -> variant) so variants run under similar API
  conditions instead of one variant first and the other an hour later.

Usage:
  python eval/run_eval.py --exp m3_vs_baseline --variants baseline m3 --repeats 2 --workers 2
  python eval/run_eval.py --exp m3_vs_baseline --dry-run        # list jobs and what is already done
Then: python eval/report.py --exp m3_vs_baseline
"""

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.build import BuildConfig, run_build  # noqa: E402

VARIANTS = {
    "baseline": BuildConfig(edit_tools=False, compact_context=False),  # before M3 (full rewrites, full history)
    "m3": BuildConfig(),                                               # edit_script + history compaction
}

parser = argparse.ArgumentParser()
parser.add_argument("--exp", required=True, help="experiment name -> runs/eval/<exp>/")
parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=list(VARIANTS))
parser.add_argument("--tasks", nargs="*", help="task ids (default: all in eval/tasks.json)")
parser.add_argument("--repeats", type=int, default=2)
parser.add_argument("--workers", type=int, default=2, help="parallel jobs (each runs its own Blender)")
parser.add_argument("--coder-model", help="override coder model for all variants")
parser.add_argument("--critic-model", help="override critic/verifier model for all variants")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

tasks = json.loads((ROOT / "eval" / "tasks.json").read_text(encoding="utf-8"))
if args.tasks:
    tasks = [t for t in tasks if t["id"] in args.tasks]
exp_dir = ROOT / "runs" / "eval" / args.exp


def config_for(variant: str) -> BuildConfig:
    cfg = VARIANTS[variant]
    if args.coder_model:
        cfg = replace(cfg, coder_model=args.coder_model)
    if args.critic_model:
        cfg = replace(cfg, critic_model=args.critic_model)
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

exp_dir.mkdir(parents=True, exist_ok=True)
commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
manifest = {"exp": args.exp, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "git_commit": commit,
            "repeats": args.repeats, "tasks": [t["id"] for t in tasks],
            "variants": {v: asdict(config_for(v)) for v in args.variants}}
(exp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

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
        rec = run_build(task["prompt"], config_for(variant), out, log=log)
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
