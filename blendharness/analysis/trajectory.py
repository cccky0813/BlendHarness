"""Trajectories: rebuild what happened in one build from its files (trace.jsonl + result.json).

A run directory already holds everything: result.json has the outcome per round (critic issues, verifier
verdicts), trace.jsonl has every agent run, LLM call and tool call as spans. This module turns them into
one structured record per build, the input for metrics, failure analysis and the Reflector.

Works on runs from before and after the 2026-10 restructuring (newer traces simply carry more attributes).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from blendharness.core.context_engine.token import CHARS_PER_TOKEN

EXEC_TOOLS = {"execute_bpy", "edit_script"}


@dataclass
class ToolStep:
    name: str
    is_error: bool
    output: str                  # preview (first ~300 chars)
    blocked_by_guard: bool = False


@dataclass
class CoderRun:
    """One AgentLoop run of the Coder: round 0 builds, later rounds revise."""
    round: int
    status: str                  # done / unverified / stuck / over_budget / truncated / max_steps
    steps: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int        # as reported by the provider (our relay reports 0)
    hidden_tokens_est: int       # output tokens minus the visible reply (text + tool arguments): ~thinking
    tools: list[ToolStep] = field(default_factory=list)
    interventions: list[str] = field(default_factory=list)   # rail decisions / completion nudges

    @property
    def exec_attempts(self) -> list[ToolStep]:
        return [t for t in self.tools if t.name in EXEC_TOOLS]

    @property
    def recovered(self) -> Optional[bool]:
        """None if no execution failed; else whether a later execution in the same run succeeded."""
        attempts = self.exec_attempts
        first_error = next((i for i, t in enumerate(attempts) if t.is_error), None)
        if first_error is None:
            return None
        return any(not t.is_error for t in attempts[first_error + 1:])


@dataclass
class Issue:
    """One problem found by the Critic (or a regression found by the Verifier), followed across rounds."""
    text: str
    raised_in: int                         # round
    verdicts: list[str] = field(default_factory=list)   # one per revision round it was open in
    regression: bool = False
    category: str = ""                     # filled by analysis.taxonomy
    fix_hint: str = ""                     # the Critic's suggested fix
    evidence: list[str] = field(default_factory=list)   # the Verifier's reason, per revision round

    @property
    def fixed(self) -> bool:
        return bool(self.verdicts) and self.verdicts[-1] == "FIXED"


@dataclass
class Trajectory:
    run_dir: str
    experiment: str              # eval experiment name, or "single"
    variant: str
    task_id: str
    prompt: str
    config: dict[str, Any]
    approved: bool
    status: str
    approved_in_round0: bool
    n_rounds: int
    cost_usd: float
    wall_time_s: float
    coder_runs: list[CoderRun] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    critic_cost_usd: float = 0.0
    verifier_cost_usd: float = 0.0


def find_runs(root: str | Path) -> Iterator[Path]:
    """Every build directory under `root` (has result.json with per-round records)."""
    for result in sorted(Path(root).glob("**/result.json")):
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("rounds") and (result.parent / "trace.jsonl").exists():
            yield result.parent


def load_trajectory(run_dir: str | Path, runs_root: str | Path = "runs") -> Trajectory:
    run_dir = Path(run_dir)
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    spans = _read_spans(run_dir / "trace.jsonl")
    experiment, variant, task_id = _identity(run_dir, Path(runs_root))
    rounds = result.get("rounds") or []
    per_agent = result.get("llm_per_agent") or {}
    return Trajectory(
        run_dir=str(run_dir), experiment=experiment, variant=variant, task_id=task_id,
        prompt=result.get("prompt", ""), config=result.get("config") or {},
        approved=bool(result.get("approved")), status=result.get("status", ""),
        approved_in_round0=bool(rounds and rounds[0].get("critic_approved")),
        n_rounds=len(rounds), cost_usd=(result.get("llm_total") or {}).get("cost_usd") or 0.0,
        wall_time_s=result.get("wall_time_s") or 0.0,
        coder_runs=_coder_runs(spans, run_dir), issues=_issue_lifecycle(rounds),
        critic_cost_usd=(per_agent.get("critic") or {}).get("cost_usd") or 0.0,
        verifier_cost_usd=(per_agent.get("verifier") or {}).get("cost_usd") or 0.0,
    )


# ---------- internals ----------
def _read_spans(path: Path) -> list[dict]:
    spans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError:
            continue                   # a line cut off by an interruption
    return spans


def _identity(run_dir: Path, runs_root: Path) -> tuple[str, str, str]:
    """runs/eval/<exp>/<variant>/<task>_r<k>  or  runs/<single run>."""
    try:
        parts = run_dir.resolve().relative_to(runs_root.resolve()).parts
    except ValueError:
        parts = run_dir.parts[-4:]
    if len(parts) >= 4 and parts[0] == "eval":
        return parts[1], parts[2], parts[3].rsplit("_r", 1)[0]
    return "single", "single", run_dir.name


def _coder_runs(spans: list[dict], run_dir: Path) -> list[CoderRun]:
    coder_spans = sorted((s for s in spans if s["kind"] == "agent" and s["name"] == "coder"),
                         key=lambda s: s["start_time"])
    runs = []
    for rnd, agent in enumerate(coder_spans):
        children = [s for s in spans if s.get("parent_id") == agent["span_id"]]
        llm_spans = [s for s in children if s["kind"] == "llm"]
        llm = [s["attrs"] for s in llm_spans]
        tools = sorted((s for s in children if s["kind"] == "tool"), key=lambda s: s["start_time"])
        attrs = agent["attrs"]
        runs.append(CoderRun(
            round=rnd, status=str(attrs.get("status", "")), steps=int(attrs.get("steps") or len(llm)),
            cost_usd=sum(a.get("cost_usd") or 0 for a in llm),
            input_tokens=sum(a.get("input_tokens") or 0 for a in llm),
            output_tokens=sum(a.get("output_tokens") or 0 for a in llm),
            reasoning_tokens=sum(a.get("reasoning_tokens") or 0 for a in llm),
            hidden_tokens_est=sum(_hidden_tokens(s, run_dir) for s in llm_spans),
            tools=[_tool_step(t) for t in tools],
            interventions=[f"{k}: {v}" for k, v in attrs.items()
                           if k.startswith("rail_step") or k.startswith("completion_rejected")],
        ))
    return runs


def _hidden_tokens(span: dict, run_dir: Path) -> int:
    """Output tokens the reply does not show (thinking), estimated from the saved full reply.

    Providers behind our relay report reasoning_tokens = 0, so: output_tokens - tokens of the visible reply
    (text + tool-call arguments), converted with the calibrated chars-per-token ratio."""
    attrs = span["attrs"]
    if attrs.get("reasoning_tokens"):
        return attrs["reasoning_tokens"]
    payload = run_dir / "payloads" / f"{span['span_id']}.json"
    try:
        reply = json.loads(payload.read_text(encoding="utf-8"))["response"]
    except (OSError, json.JSONDecodeError, KeyError):
        return 0
    visible = len(reply.get("content") or "") + sum(len(tc["function"].get("arguments") or "")
                                                     for tc in reply.get("tool_calls") or [])
    return max(0, (attrs.get("output_tokens") or 0) - round(visible / CHARS_PER_TOKEN))


def _tool_step(span: dict) -> ToolStep:
    out = str(span["attrs"].get("output_preview", ""))
    is_error = span["attrs"].get("is_error")
    is_error = is_error if isinstance(is_error, bool) else str(is_error) == "True" or out.startswith("ERROR")
    return ToolStep(span["name"], is_error, out, blocked_by_guard="sandbox policy" in out)


def _issue_lifecycle(rounds: list[dict]) -> list[Issue]:
    """Follow every issue through the revision rounds, exactly as the pipeline did: the verifier judges the
    currently open issues by index; FIXED ones are dropped, the rest stay open, regressions are appended."""
    if not rounds:
        return []
    all_issues = [Issue(i.get("problem", ""), 0, fix_hint=i.get("fix", "")) for i in rounds[0].get("issues") or []]
    open_issues = list(all_issues)
    for rd in rounds[1:]:
        verdicts = rd.get("verdicts") or []
        if rd.get("verified") and not verdicts:            # passed without per-issue detail
            verdicts = [{"index": i + 1, "verdict": "FIXED"} for i in range(len(open_issues))]
        still_open = []
        for v in verdicts:
            idx = v.get("index", 0)
            if 0 < idx <= len(open_issues):
                issue = open_issues[idx - 1]
                issue.verdicts.append(str(v.get("verdict", "")))
                issue.evidence.append(str(v.get("evidence", "")))
                if issue.verdicts[-1] != "FIXED":
                    still_open.append(issue)
        regressions = [Issue(r, rd.get("round", 0), regression=True) for r in rd.get("regressions") or []]
        all_issues += regressions
        open_issues = still_open + regressions
    return all_issues
