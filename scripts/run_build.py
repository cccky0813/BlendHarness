"""Coder + Critic refinement loop on one prompt.

    coder builds -> render -> critic reviews -> (issues?) coder revises with the critique -> ... (max N rounds)

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
from harness.llm import LLMClient  # noqa: E402
from harness.tracing import Tracer  # noqa: E402
from tools.blender_session import BlenderSession  # noqa: E402
from tools.blender_tools import BlenderTools  # noqa: E402

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

A visual critic reviewed renders of this model and found these problems:
{issues}

Fix these problems, keeping everything that is already good: run the complete revised script with
execute_bpy, check it with inspect_scene, and only then reply with a short summary."""

parser = argparse.ArgumentParser()
parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
parser.add_argument("--model", default="deepseek-flash")
parser.add_argument("--critic-model", default="claude-haiku-4-5")
parser.add_argument("--rounds", type=int, default=2, help="max critic-driven revisions")
parser.add_argument("--effort", default="low", choices=["default", "low", "high", "off"])
args = parser.parse_args()

EXTRA = {"default": None, "low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"},
         "off": {"thinking": {"type": "disabled"}}}[args.effort]
run_id = time.strftime("%Y%m%d_%H%M%S") + f"_build_{args.model}"
run_dir = Path("runs") / run_id
tracer = Tracer(run_dir, run_id=run_id)
t0 = time.perf_counter()
rounds_log = []

with BlenderSession(run_dir / "blender.log") as session:
    blender = BlenderTools(session, run_dir / "renders")
    coder = make_coder(LLMClient(args.model, tracer,
                                 extra_body=EXTRA if args.model.startswith("deepseek") else None), blender)
    # reasoning_effort / thinking flags are DeepSeek-specific: only pass them to DeepSeek models
    critic_llm = LLMClient(args.critic_model, tracer,
                           extra_body=EXTRA if args.critic_model.startswith("deepseek") else None)

    with tracer.span("run", "build", prompt=args.prompt):
        task = args.prompt
        for rnd in range(args.rounds + 1):
            result = coder.run(task)
            if not blender.current_script:
                rounds_log.append({"round": rnd, "coder_status": result.status, "note": "no working script"})
                break
            renders = blender.render(f"round{rnd}", num_views=3)
            verdict = critique(critic_llm, args.prompt, renders, blender.inspect_scene().content)
            rounds_log.append({"round": rnd, "coder_status": result.status, "coder_steps": result.steps,
                               "renders": renders, "approved": verdict.approved, "issues": verdict.issues,
                               "critic_parse_error": verdict.parse_error})
            print(f"--- round {rnd}: coder={result.status} ({result.steps} steps) | critic: "
                  f"{'APPROVED' if verdict.approved else str(len(verdict.issues)) + ' issues'}")
            for it in verdict.issues:
                print(f"    - {it.get('problem')}\n      fix: {it.get('fix')}")
            if verdict.approved or rnd == args.rounds:
                break
            task = REFINE_TEMPLATE.format(request=args.prompt, script=blender.current_script,
                                          issues=verdict.as_text())

    (run_dir / "final_script.py").write_text(blender.current_script, encoding="utf-8")
    total = tracer.summary()
    record = {"prompt": args.prompt, "models": {"coder": args.model, "critic": args.critic_model},
              "rounds": rounds_log, "approved": bool(rounds_log and rounds_log[-1].get("approved")),
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
