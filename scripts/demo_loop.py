"""Demo of the agent loop (blendharness/core/runtime) with fake tools (no Blender yet).

Scenario 1: normal multi-step tool use -> status "done".
Scenario 2: a tool that always fails with the same error -> status "stuck".
Prints each run's trace as a tree.
"""

import json
import sys
from pathlib import Path


from blendharness.core.llm import LLMClient
from blendharness.core.runtime import AgentLoop
from blendharness.core.tools import Tool, ToolResult
from blendharness.core.tracing import Tracer

MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-flash"

WEATHER = {"Singapore": "32°C, thunderstorms", "Beijing": "18°C, sunny"}

get_weather = Tool(
    name="get_weather",
    description="Get the current weather for a city.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    fn=lambda city: WEATHER.get(city, ToolResult(f"ERROR: no data for city '{city}'", True)),
)

multiply = Tool(
    name="multiply",
    description="Multiply two numbers exactly.",
    parameters={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"]},
    fn=lambda a, b: str(a * b),
)

broken_render = Tool(
    name="render_scene",
    description="Render the current 3D scene to an image.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=lambda: ToolResult("ERROR: GPU device not found", is_error=True),
)


def print_tree(trace_path: Path) -> None:
    spans = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    children: dict = {}
    for s in spans:
        children.setdefault(s["parent_id"], []).append(s)

    def show(parent_id, depth):
        for s in sorted(children.get(parent_id, []), key=lambda x: x["start_time"]):
            a = s["attrs"]
            info = {
                "agent": f"status={a.get('status')} steps={a.get('steps')} ${a.get('cost_usd')}",
                "llm": f"in={a.get('input_tokens')} out={a.get('output_tokens')} "
                       f"calls={[c['name'] for c in a.get('tool_calls', [])]}",
                "tool": f"args={a.get('args')} -> {'ERROR' if a.get('is_error') else 'ok'}: "
                        f"{(a.get('output_preview') or '')[:40]}",
            }.get(s["kind"], "")
            print("  " * depth + f"{s['kind']}:{s['name']} ({s['latency_s']}s) {info}")
            show(s["span_id"], depth + 1)

    show(None, 0)


for title, tools, task in [
    ("Scenario 1: normal", [get_weather, multiply],
     "How much warmer is Singapore than Beijing right now, in °C? Then multiply that difference by 37."),
    ("Scenario 2: always-failing tool", [broken_render],
     "Render the scene. If rendering fails, try again until it works."),
]:
    run_dir = Path("runs") / f"demo_loop_{title.split(':')[0].replace(' ', '').lower()}"
    tracer = Tracer(run_dir)
    agent = AgentLoop("demo", LLMClient(MODEL, tracer), "You are a helpful assistant. Use tools when needed.",
                      tools, max_steps=8, max_same_error=3)
    result = agent.run(task)
    print(f"\n===== {title} =====")
    print(f"status={result.status} steps={result.steps} cost=${result.cost_usd:.5f} reason={result.reason!r}")
    print("final:", result.final_text[:200].replace("\n", " "))
    print("--- trace tree ---")
    print_tree(tracer.path)
