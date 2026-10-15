"""Run the Coder + Critic + Verifier pipeline (pipeline/build.py) on one prompt from the command line.

Usage: python scripts/run_build.py "prompt" [--model claude-sonnet-4-6] [--critic-model claude-haiku-4-5]
                                   [--rounds 2] [--effort low] [--no-edit] [--no-compact]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.build import BuildConfig, run_build  # noqa: E402

DEFAULT_PROMPT = (
    "Create a realistic ceramic coffee mug: cylindrical body with a hollow open top and visible wall "
    "thickness, a C-shaped handle attached to the side, glossy white ceramic material, and dark coffee "
    "liquid inside."
)

parser = argparse.ArgumentParser()
parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
parser.add_argument("--model", default="claude-sonnet-4-6")
parser.add_argument("--critic-model", default="claude-haiku-4-5")
parser.add_argument("--rounds", type=int, default=2, help="max revision rounds")
parser.add_argument("--no-edit", action="store_true", help="ablation: no edit_script/view_script tools")
parser.add_argument("--no-compact", action="store_true", help="ablation: no context compaction")
parser.add_argument("--effort", default="low", choices=["default", "low", "high", "off"], help="coder thinking effort")
parser.add_argument("--critic-effort", default="default", choices=["default", "low", "high", "off"],
                    help="critic/verifier thinking effort; default = provider default (Haiku: no thinking)")
args = parser.parse_args()

cfg = BuildConfig(coder_model=args.model, critic_model=args.critic_model, rounds=args.rounds,
                  coder_effort=args.effort, critic_effort=args.critic_effort,
                  edit_tools=not args.no_edit, compact_context=not args.no_compact)
variant = ("noedit_" if args.no_edit else "") + ("nocompact_" if args.no_compact else "")
run_dir = Path("runs") / (time.strftime("%Y%m%d_%H%M%S") + f"_build_{variant}{args.model}")

record = run_build(args.prompt, cfg, run_dir)

print(f"\napproved={record['approved']} | rounds={record['n_rounds']} | time={record['wall_time_s']}s | "
      f"executions={record['executions']} (failed {record['failed_executions']})")
for agent, row in record["llm_per_agent"].items():
    print(f"  {agent:<8} calls={row['calls']:<3} in={row['input_tokens']:<7} out={row['output_tokens']:<6} "
          f"${row['cost_usd']:.4f}  {row['latency_s']:.0f}s")
print(f"  TOTAL    ${record['llm_total']['cost_usd']:.4f}\nrun dir: {run_dir}")
