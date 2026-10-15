"""Aggregate an experiment (runs/eval/<exp>/) into a markdown report.

Per variant: pass rate, rounds, executions, cost, time; per tier; the fairest M3 metric
(coder cost / output tokens per REVISION round, read from the traces); and failure analysis
(execution error types + categories of issues still open at the end).

Usage: python eval/report.py --exp m3_vs_baseline
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser()
parser.add_argument("--exp", required=True)
args = parser.parse_args()
exp_dir = ROOT / "runs" / "eval" / args.exp
tiers = {t["id"]: t["tier"] for t in json.loads((ROOT / "eval" / "tasks.json").read_text(encoding="utf-8"))}

ISSUE_CATEGORIES = [  # first match wins; keyword lists are deliberately simple and inspectable
    ("floating / detached", r"float|detach|not attached|hover|mid-air|gap|not touching|not connected"),
    ("intersecting / buried / hidden", r"intersect|buried|inside the|embedded|z-fight|coplanar|hidden|swallow|penetrat"),
    ("missing part", r"missing|absent|no \w+ (is )?visible|not visible|invisible|does not exist|no visible"),
    ("proportion / scale", r"scale|proportion|oversiz|too (large|big|small|thin|thick|wide|tall|short)|size"),
    ("material / color", r"material|colou?r|texture|emissi"),
    ("shape / form", r"shape|primitive|placeholder|box|cylinder|flat"),
]


def issue_category(text: str) -> str:
    t = text.lower()
    for name, pattern in ISSUE_CATEGORIES:
        if re.search(pattern, t):
            return name
    return "other"


def exec_error_type(err: str) -> str:
    if err.startswith("TIMEOUT"):
        return "timeout"
    if "EMPTY scene" in err:
        return "empty-scene guard"
    m = re.match(r"(?:ERROR: )?(\w+Error|\w+Exception)", err)
    return m.group(1) if m else err.split(":")[0][:40]


def coder_rounds(run_dir: Path) -> list[dict]:
    """Cost / output tokens of each coder run (round 0 = first build, then revisions), from the trace."""
    spans = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    coder_runs = sorted((s for s in spans if s["kind"] == "agent" and s["name"] == "coder"), key=lambda s: s["start_time"])
    rows = []
    for run in coder_runs:
        llm = [s for s in spans if s["kind"] == "llm" and s["parent_id"] == run["span_id"]]
        rows.append({"cost": sum(s["attrs"].get("cost_usd") or 0 for s in llm),
                     "out": sum(s["attrs"].get("output_tokens") or 0 for s in llm),
                     "in": sum(s["attrs"].get("input_tokens") or 0 for s in llm)})
    return rows


def mean(xs):
    return statistics.mean(xs) if xs else float("nan")


def fmt(x, money=False):
    if x != x:  # NaN
        return "-"
    return f"${x:.3f}" if money else f"{x:.1f}"


results = defaultdict(list)   # variant -> list of (task_id, record, run_dir)
crashes = defaultdict(list)
for variant_dir in sorted(p for p in exp_dir.iterdir() if p.is_dir()):
    for run_dir in sorted(p for p in variant_dir.iterdir() if p.is_dir()):
        task_id = run_dir.name.rsplit("_r", 1)[0]
        if (run_dir / "result.json").exists():
            results[variant_dir.name].append((task_id, json.loads((run_dir / "result.json").read_text()), run_dir))
        elif (run_dir / "crash.json").exists():
            crashes[variant_dir.name].append((task_id, json.loads((run_dir / "crash.json").read_text())["error"]))

lines = [f"# Eval report: {args.exp}", ""]
manifest = exp_dir / "manifest.json"
if manifest.exists():
    m = json.loads(manifest.read_text())
    lines += [f"git commit `{m['git_commit']}`, started {m['started']}, repeats {m['repeats']}", ""]

lines += ["## Overall", "",
          "| variant | runs | passed | pass rate | avg rounds | avg execs (failed) | avg total cost | avg coder cost | "
          "**coder $ / revision round** | **coder out tokens / revision round** | avg time | crashes |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for variant, rows in results.items():
    recs = [r for _, r, _ in rows]
    rev = [rd for _, _, d in rows for rd in coder_rounds(d)[1:]]
    lines.append(
        f"| {variant} | {len(recs)} | {sum(r['approved'] for r in recs)} | "
        f"{100 * mean([r['approved'] for r in recs]):.0f}% | {fmt(mean([r['n_rounds'] for r in recs]))} | "
        f"{fmt(mean([r['executions'] for r in recs]))} ({fmt(mean([r['failed_executions'] for r in recs]))}) | "
        f"{fmt(mean([r['llm_total']['cost_usd'] for r in recs]), True)} | "
        f"{fmt(mean([r['llm_per_agent'].get('coder', {}).get('cost_usd', 0) for r in recs]), True)} | "
        f"{fmt(mean([x['cost'] for x in rev]), True)} (n={len(rev)}) | {fmt(mean([x['out'] for x in rev]))} | "
        f"{fmt(mean([r['wall_time_s'] for r in recs]))}s | {len(crashes.get(variant, []))} |")

lines += ["", "## By task (pass count / runs, avg total cost)", "",
          "| task | tier | " + " | ".join(results) + " |", "|---|---|" + "---|" * len(results)]
for task_id in tiers:
    cells = []
    for variant, rows in results.items():
        recs = [r for t, r, _ in rows if t == task_id]
        cells.append(f"{sum(r['approved'] for r in recs)}/{len(recs)}, "
                     f"{fmt(mean([r['llm_total']['cost_usd'] for r in recs]), True)}" if recs else "-")
    lines.append(f"| {task_id} | {tiers[task_id]} | " + " | ".join(cells) + " |")

lines += ["", "## Failure analysis", ""]
for variant, rows in results.items():
    errs = Counter(exec_error_type(e) for _, r, _ in rows for e in r.get("exec_errors", []))
    detail = Counter(e[:110] for _, r, _ in rows for e in r.get("exec_errors", []))
    first_issues = Counter(issue_category(i.get("problem", "")) for _, r, _ in rows
                           for i in (r["rounds"][0].get("issues", []) if r["rounds"] else []))
    still_open = Counter(issue_category(i.get("problem", "")) for _, r, _ in rows for i in r.get("open_issues", []))
    lines += [f"### {variant}", "",
              f"- execution errors by type: {dict(errs.most_common()) or 'none'}",
              f"- most frequent execution errors: " + ("; ".join(f"`{e}` x{n}" for e, n in detail.most_common(4)) or "none"),
              f"- critic issues found in round 0, by category: {dict(first_issues.most_common()) or 'none'}",
              f"- issues still open at the end, by category: {dict(still_open.most_common()) or 'none'}", ""]
    for task_id, err in crashes.get(variant, []):
        lines.append(f"- CRASH {task_id}: {err[:160]}")

report = "\n".join(lines) + "\n"
(exp_dir / "report.md").write_text(report, encoding="utf-8")
print(report)
print(f"saved {exp_dir / 'report.md'}")
