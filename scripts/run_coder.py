"""End-to-end: one Coder agent builds an object in the Blender sandbox, then we render it.

Usage: python scripts/run_coder.py "Create a ceramic coffee mug ..." [--model deepseek-flash]
Outputs (runs/<run_id>/): trace.jsonl, payloads/, final_script.py, renders/*.png, result.json
"""

import argparse
import json
import time
from pathlib import Path


from blendharness.agents.coder import make_coder
from blendharness.core.llm import LLMClient
from blendharness.core.tracing import Tracer
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.blender_tools import BlenderTools

DEFAULT_PROMPT = (
    "Create a realistic ceramic coffee mug: cylindrical body with a hollow open top and visible wall "
    "thickness, a C-shaped handle attached to the side, glossy white ceramic material, and dark coffee "
    "liquid inside."
)

parser = argparse.ArgumentParser()
parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
parser.add_argument("--model", default="deepseek-flash")
parser.add_argument("--max-steps", type=int, default=15)
parser.add_argument("--effort", default="default", choices=["default", "low", "high", "off"],
                    help="DeepSeek thinking: default/low/high = reasoning_effort, off = thinking disabled")
args = parser.parse_args()

EXTRA = {"default": None, "low": {"reasoning_effort": "low"}, "high": {"reasoning_effort": "high"},
         "off": {"thinking": {"type": "disabled"}}}[args.effort]
run_id = time.strftime("%Y%m%d_%H%M%S") + f"_coder_{args.effort}"
run_dir = Path("runs") / run_id
tracer = Tracer(run_dir, run_id=run_id)
t0 = time.perf_counter()

with BlenderSession(run_dir / "blender.log") as session:
    blender = BlenderTools(session, run_dir / "renders")
    coder = make_coder(LLMClient(args.model, tracer, extra_body=EXTRA), blender, max_steps=args.max_steps)

    with tracer.span("run", "coder_e2e", prompt=args.prompt, model=args.model):
        result = coder.run(args.prompt)
        with tracer.span("tool", "render_final"):
            renders = blender.render("final", num_views=3)

    (run_dir / "final_script.py").write_text(blender.current_script, encoding="utf-8")
    summary = tracer.summary()
    record = {
        "prompt": args.prompt, "model": args.model, "effort": args.effort, "status": result.status, "reason": result.reason,
        "steps": result.steps, "wall_time_s": round(time.perf_counter() - t0, 1),
        "executions": len(blender.history),
        "failed_executions": sum(not h["ok"] for h in blender.history),
        "sandbox_restarts": session.restarts,
        "llm": summary["total"],
        "reasoning_tokens": sum(sp.attrs.get("reasoning_tokens", 0) or 0 for sp in tracer.spans if sp.kind == "llm"), "renders": renders, "final_text": result.final_text,
    }
    (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

print(json.dumps({k: v for k, v in record.items() if k != "final_text"}, ensure_ascii=False, indent=2))
print("\nfinal answer:", result.final_text[:400])
print("\ntool calls in order:")
for s in tracer.spans:
    if s.kind == "tool":
        print(f"  {s.name:<14} {'ERROR' if s.attrs.get('is_error') else 'ok   '} {s.latency_s:>5}s "
              f"{(s.attrs.get('output_preview') or '')[:90]!r}")
