"""Recompute the cost of past runs from their traces with the current config/prices.json.

Usage: python scripts/recost.py runs/<run_dir> [runs/<run_dir> ...]
Useful when prices were unknown at run time: tokens are in the trace, so cost can be derived later.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


from blendharness.core.llm import load_prices

prices = load_prices()
for run_dir in sys.argv[1:]:
    per_agent: dict = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
    for line in (Path(run_dir) / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        if s["kind"] != "llm":
            continue
        a, p = s["attrs"], prices.get(s["name"])
        inp, out, cached = a.get("input_tokens") or 0, a.get("output_tokens") or 0, a.get("cached_tokens") or 0
        row = per_agent[a.get("agent", "?")]
        row["calls"] += 1
        row["in"] += inp
        row["out"] += out
        if p:
            row["cost"] += ((inp - cached) * p["input"] + cached * p.get("cached_input", p["input"])
                            + out * p["output"]) / 1e6
    total = sum(r["cost"] for r in per_agent.values())
    print(f"{run_dir}: ${total:.4f}")
    for agent, r in per_agent.items():
        print(f"   {agent:<7} calls={r['calls']:<3} in={r['in']:<7} out={r['out']:<6} ${r['cost']:.4f}")
