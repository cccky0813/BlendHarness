"""Coder + Critic + Verifier on one prompt.

    coder builds -> render -> critic (open question: what is wrong?)
      -> coder revises -> verifier (closed question: was each issue fixed? regressions?)
      -> unresolved issues only -> coder revises -> verifier ... (max N revision rounds)

Usage: python scripts/run_build.py "prompt" [--model deepseek-flash] [--critic-model claude-haiku-4-5] [--rounds 2]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.coder import make_coder  # noqa: E402
from agents.critic import critique  # noqa: E402
from agents.verifier import verify  # noqa: E402
from harness.llm import LLMClient  # noqa: E402
from harness.tracing import Tracer  # noqa: E402
from tools.blender_session import BlenderSession  # noqa: E402
from tools.blender_tools import BlenderTools, diff_snapshots  # noqa: E402

DEFAULT_PROMPT = (
    "Create a realistic ceramic coffee mug: cylindrical body with a hollow open top and visible wall "
    "thickness, a C-shaped handle attached to the side, glossy white ceramic material, and dark coffee "
    "liquid inside."
)

REFINE_TEMPLATE = """Revise an existing Blender model.

Original request:
{request}

Current script (it runs without errors):
```python
{script}
```

A visual review of renders of this model found these problems:
{issues}

Fix these problems, keeping everything that is already good. The script above is already loaded as
the current working script: make each fix with edit_script (exact old_string -> new_string, one call per
separate change) instead of resending the whole script. Then check with inspect_scene, and only then reply
with a short summary."""

REFINE_TEMPLATE_NO_EDIT = REFINE_TEMPLATE.replace(
    "The script above is already loaded as\nthe current working script: make each fix with edit_script (exact old_string -> new_string, one call per\nseparate change) instead of resending the whole script.",
    "Run the complete revised script with execute_bpy.")

parser = argparse.ArgumentParser()
parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
parser.add_argument("--model", default="deepseek-flash")
parser.add_argument("--critic-model", default="claude-haiku-4-5")
parser.add_argument("--rounds", type=int, default=2, help="max critic-driven revisions")
parser.add_argument("--no-edit", action="store_true", help="ablation: no edit_script/view_script tools")
parser.add_argument("--no-compact", action="store_true", help="ablation: no context compaction")
parser.add_argument("--effort", default="low", choices=["default", "low", "high", "off"],
                    help="coder thinking effort")
parser.add_argument("--critic-effort", default="default", choices=["default", "low", "high", "off"],
                    help="critic thinking effort; default = provider default (Haiku: no thinking)")
args = parser.parse_args()



def effort_body(model: str, effort: str) -> dict | None:
    """Thinking control. `reasoning_effort` works for DeepSeek and for Claude through the relay
    (measured: Claude default thinking burned 12k-32k tokens before acting; low -> ~1.7k).
    Disabling thinking entirely is only supported by DeepSeek.
    Careful: the same flag means different things per model. Sonnet thinks by default, so "low" REDUCES
    thinking; Haiku does not think by default, so "low" TURNS IT ON (critic went 13 -> 32k tokens).
    Hence effort is configured per agent, not globally."""
    if effort in ("low", "high"):
        return {"reasoning_effort": effort}
    if effort == "off" and model.startswith("deepseek"):
        return {"thinking": {"type": "disabled"}}
    return None

variant = ("noedit_" if args.no_edit else "") + ("nocompact_" if args.no_compact else "")
run_id = time.strftime("%Y%m%d_%H%M%S") + f"_build_{variant}{args.model}"
run_dir = Path("runs") / run_id
tracer = Tracer(run_dir, run_id=run_id)
t0 = time.perf_counter()
rounds_log = []

with BlenderSession(run_dir / "blender.log") as session:
    blender = BlenderTools(session, run_dir / "renders")
    coder = make_coder(LLMClient(args.model, tracer, extra_body=effort_body(args.model, args.effort)), blender,
                       compact_context=not args.no_compact, edit_tools=not args.no_edit)
    critic_llm = LLMClient(args.critic_model, tracer, extra_body=effort_body(args.critic_model, args.critic_effort))
    verifier_llm = critic_llm  # same model by default; still a separate role with its own prompt

    with tracer.span("run", "build", prompt=args.prompt):
        # Round 0: build from the prompt, then an open-ended critique.
        result = coder.run(args.prompt)
        approved = False
        if blender.current_script:
            renders = blender.render("round0", num_views=3)
            verdict = critique(critic_llm, args.prompt, renders, blender.inspect_scene().content)
            approved = verdict.approved
            issues = verdict.issues
            rounds_log.append({"round": 0, "coder_status": result.status, "coder_steps": result.steps,
                               "renders": renders, "critic_approved": verdict.approved, "issues": issues})
            print(f"--- round 0: coder={result.status} ({result.steps} steps) | critic: "
                  f"{'APPROVED' if approved else str(len(issues)) + ' issues'}")
            for it in issues:
                print(f"    - {it.get('problem')}")
            feedback = verdict.as_text()

            # Revision rounds: Coder fixes, Verifier checks each issue (closed question).
            for rnd in range(1, args.rounds + 1):
                if approved:
                    break
                before_snapshot, before_renders = blender.snapshot(), renders
                result = coder.run((REFINE_TEMPLATE_NO_EDIT if args.no_edit else REFINE_TEMPLATE).format(request=args.prompt, script=blender.current_script,
                                                          issues=feedback))
                renders = blender.render(f"round{rnd}", num_views=3)
                diff = diff_snapshots(before_snapshot, blender.snapshot())
                check = verify(verifier_llm, args.prompt, issues, before_renders, renders, diff)
                approved = check.passed
                rounds_log.append({"round": rnd, "coder_status": result.status, "coder_steps": result.steps,
                                   "renders": renders, "diff": diff, "verified": check.passed,
                                   "verdicts": check.issues, "regressions": check.regressions,
                                   "verifier_skipped_llm": check.skipped_llm})
                print(f"--- round {rnd}: coder={result.status} ({result.steps} steps) | verifier: "
                      f"{'PASSED' if check.passed else 'FAILED'}"
                      f"{' (scene unchanged, no LLM call)' if check.skipped_llm else ''}")
                for v in check.issues:
                    print(f"    issue {v.get('index')}: {v.get('verdict')} - {str(v.get('evidence', ''))[:110]}")
                for r in check.regressions:
                    print(f"    regression: {r}")
                if not approved:
                    # Next revision only hears what is still broken (fixed issues are dropped).
                    feedback = check.unresolved_text(issues)
                    issues = [issues[v["index"] - 1] for v in check.issues
                              if v.get("verdict") != "FIXED" and 0 < v.get("index", 0) <= len(issues)]
                    issues += [{"problem": f"Regression: {r}", "fix": "Restore what was correct before."}
                               for r in check.regressions]
        else:
            rounds_log.append({"round": 0, "coder_status": result.status, "note": "no working script"})

    (run_dir / "final_script.py").write_text(blender.current_script, encoding="utf-8")
    total = tracer.summary()
    record = {"prompt": args.prompt, "models": {"coder": args.model, "critic": args.critic_model, "verifier": args.critic_model},
              "m3": {"edit_tools": not args.no_edit, "compact_context": not args.no_compact},
              "rounds": rounds_log, "approved": approved,
              "wall_time_s": round(time.perf_counter() - t0, 1), "executions": len(blender.history),
              "failed_executions": sum(not h["ok"] for h in blender.history),
              "llm_total": total["total"], "llm_per_agent": total["per_agent"]}
    (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\napproved={record['approved']} | rounds={len(rounds_log)} | time={record['wall_time_s']}s | "
      f"executions={record['executions']} (failed {record['failed_executions']})")
for agent, row in total["per_agent"].items():
    print(f"  {agent:<7} calls={row['calls']:<3} in={row['input_tokens']:<7} out={row['output_tokens']:<6} "
          f"${row['cost_usd']:.4f}  {row['latency_s']:.0f}s")
print(f"  TOTAL   ${total['total']['cost_usd']:.4f}\nrun dir: {run_dir}")
