"""Smoke test for blendharness/core/llm: plain chat, function calling, and error tracing.

Usage: python scripts/smoke_llm.py [model]
"""

import json
import sys


from blendharness.core.llm import LLMClient
from blendharness.core.tracing import Tracer

MODEL = sys.argv[1] if len(sys.argv) > 1 else "deepseek-flash"

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name, e.g. Singapore"}},
            "required": ["city"],
        },
    },
}

tracer = Tracer("runs/smoke_llm")
llm = LLMClient(MODEL, tracer)

print(f"== 1. plain chat ({MODEL})")
r = llm.chat([{"role": "user", "content": "Reply with exactly: pong"}], agent="smoke")
print("text:", repr(r.text), "| tokens in/out:", r.input_tokens, r.output_tokens, "| cost:", r.cost_usd)

print("\n== 2. function calling")
r = llm.chat(
    [{"role": "user", "content": "What's the weather in Singapore right now? Use the tool."}],
    tools=[WEATHER_TOOL],
    agent="smoke",
)
print("finish_reason:", r.finish_reason)
print("tool_calls:", [(c.name, c.arguments) for c in r.tool_calls])
print("FUNCTION CALLING SUPPORTED" if r.tool_calls else "NO TOOL CALL RETURNED -> check relay support")

print("\n== 3. error is traced (wrong model name)")
bad = LLMClient("no-such-model-xyz", tracer)
try:
    bad.chat([{"role": "user", "content": "hi"}], agent="smoke")
except Exception as exc:
    print("raised as expected:", type(exc).__name__)

print("\n== trace.jsonl")
for line in tracer.path.read_text(encoding="utf-8").splitlines():
    d = json.loads(line)
    print(d["kind"], d["name"], d["status"], f"{d['latency_s']}s",
          {k: d["attrs"].get(k) for k in ("input_tokens", "output_tokens", "attempts")}, d["error"] or "")
print("\nsummary:", json.dumps(tracer.summary()["total"]))
