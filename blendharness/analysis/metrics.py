"""Metrics over trajectories: outcome, efficiency, process quality and repair ability.

Outcome alone (pass rate) hides WHY a system fails or what it costs; the trace shows the process:
how often code fails to run, whether the agent recovers, how often rails had to step in, and which kinds
of problems revisions actually fix.
"""

import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable

from blendharness.analysis.taxonomy import SPATIAL
from blendharness.analysis.trajectory import Trajectory


def run_metrics(t: Trajectory) -> dict[str, Any]:
    """Per-build numbers (one row of the trajectory table)."""
    execs = [s for r in t.coder_runs for s in r.exec_attempts]
    recoveries = [r.recovered for r in t.coder_runs if r.recovered is not None]
    revisions = t.coder_runs[1:]
    return {
        "approved": t.approved, "approved_in_round0": t.approved_in_round0, "n_rounds": t.n_rounds,
        "cost_usd": t.cost_usd,
        "coder_cost_round0": t.coder_runs[0].cost_usd if t.coder_runs else 0.0,
        "coder_cost_revisions": sum(r.cost_usd for r in revisions),
        "coder_steps": sum(r.steps for r in t.coder_runs),
        "hidden_share": _share(sum(r.hidden_tokens_est for r in t.coder_runs), sum(r.output_tokens for r in t.coder_runs)),
        "hidden_share_revisions": _share(sum(r.hidden_tokens_est for r in revisions), sum(r.output_tokens for r in revisions)),
        "exec_attempts": len(execs), "exec_failures": sum(s.is_error for s in execs),
        "recovered_runs": sum(recoveries), "runs_with_exec_failure": len(recoveries),
        "guard_blocks": sum(s.blocked_by_guard for r in t.coder_runs for s in r.tools),
        "view_script_calls": sum(s.name == "view_script" for r in t.coder_runs for s in r.tools),
        "interventions": sum(len(r.interventions) for r in t.coder_runs),
        "coder_statuses": [r.status for r in t.coder_runs],
        "issues_raised": len([i for i in t.issues if not i.regression]),
        "regressions": len([i for i in t.issues if i.regression]),
    }


def aggregate(trajectories: Iterable[Trajectory]) -> dict[str, Any]:
    ts = list(trajectories)
    rows = [run_metrics(t) for t in ts]
    n = len(rows)
    statuses = Counter(s for r in rows for s in r["coder_statuses"])
    return {
        "builds": n,
        "outcome": {
            "pass_rate": _mean(r["approved"] for r in rows),
            "pass_round0": _mean(r["approved_in_round0"] for r in rows),
            "mean_rounds": _mean(r["n_rounds"] for r in rows),
        },
        "efficiency": {
            "cost_per_build": _mean(r["cost_usd"] for r in rows),
            "median_cost": statistics.median([r["cost_usd"] for r in rows]) if rows else 0.0,
            "round0_share_of_coder_cost": _share(sum(r["coder_cost_round0"] for r in rows),
                                                 sum(r["coder_cost_round0"] + r["coder_cost_revisions"] for r in rows)),
            "mean_coder_steps": _mean(r["coder_steps"] for r in rows),
            "hidden_share_of_output": _mean(r["hidden_share"] for r in rows if r["hidden_share"] is not None),
            "hidden_share_revisions": _mean(r["hidden_share_revisions"] for r in rows if r["hidden_share_revisions"] is not None),
        },
        "process": {
            "exec_failure_rate": _share(sum(r["exec_failures"] for r in rows), sum(r["exec_attempts"] for r in rows)),
            "recovery_rate": _share(sum(r["recovered_runs"] for r in rows), sum(r["runs_with_exec_failure"] for r in rows)),
            "coder_run_status": dict(statuses),
            "builds_with_rail_intervention": sum(r["interventions"] > 0 for r in rows),
            "guard_blocks": sum(r["guard_blocks"] for r in rows),
            "view_script_calls": sum(r["view_script_calls"] for r in rows),
        },
        "repair": repair_table(ts),
    }


def repair_table(trajectories: Iterable[Trajectory]) -> dict[str, dict[str, Any]]:
    """Per issue category: how often raised, how often a revision fixed it, how often it was still open
    when the build ended unapproved (= what actually makes builds fail)."""
    table: dict[str, dict[str, Any]] = defaultdict(lambda: {"raised": 0, "revised": 0, "fixed": 0, "open_at_failure": 0})
    for t in trajectories:
        for issue in t.issues:
            row = table[issue.category or "unlabelled"]
            row["raised"] += 1
            if issue.verdicts:
                row["revised"] += 1
                row["fixed"] += issue.fixed
            if not t.approved and not issue.fixed:
                row["open_at_failure"] += 1
    for row in table.values():
        row["fix_rate"] = _share(row["fixed"], row["revised"])
    return dict(sorted(table.items(), key=lambda kv: -kv[1]["raised"]))


def spatial_share(repair: dict[str, dict[str, Any]], column: str = "raised") -> float:
    total = sum(r[column] for r in repair.values())
    return _share(sum(r[column] for c, r in repair.items() if c in SPATIAL), total) or 0.0


def _mean(xs) -> float:
    xs = [float(x) for x in xs]
    return statistics.mean(xs) if xs else float("nan")


def _share(part: float, whole: float):
    return part / whole if whole else None
