# Architecture

BlendHarness is one package, `blendharness/`, in layers that only depend downward:

```
core  <-  sandbox, memory  <-  tools  <-  agents  <-  workflow
                                 tools  <-  protocol
core  <-  analysis  <-  workflow, eval
```

`core/` is a generic agent harness and knows nothing about Blender. The rule is enforced by
`tests/unit/test_architecture.py`, which parses every import.

## One request, end to end

1. **workflow/build.py** `run_build(prompt, cfg, run_dir)` creates the live objects: a `BlenderSession`
   (headless Blender in its own process), `BlenderTools`, optional `SkillTools`, LLM clients, and the
   Coder. It then runs the phases `round0 → revise(k) → learn → finalize`. Each phase ends with a checkpoint
   (`core/session`): `BuildState` is plain JSON, so `--resume` and `--fork-from` rebuild the live objects
   from it, and the scene is rebuilt by re-running `current_script`.
2. **agents/coder.py** is a configuration of `core/runtime/AgentLoop`: a prompt (`agents/prompts/coder.md`),
   tools, a context pipeline and a completion check.
3. **core/runtime/loop.py**, at every step:
   - `context_engine` builds the view from the full history (omit stale scripts, pin the current one,
     budget guard);
   - `llm.chat()` is called, inside a traced `llm` span;
   - the rails are asked at `after_llm` / `on_final_answer` (truncation, completion check);
   - each tool call runs through `ToolRegistry.execute` (error-as-observation, output truncation, traced
     `tool` span), and the rails are asked at `after_tool` / `after_step` (stuck, cost budget).
   A rail answers None, `Nudge(message)` or `Stop(status, reason)`. The loop keeps the history valid:
   any skipped tool call still gets a result.
4. **tools/blender_tools.py** `execute_bpy` checks the code with `sandbox/guard.py`, prepends loaded skills
   (a generic `preamble_provider` hook), and sends it to Blender, which resets to an empty scene first.
   Only a successful run becomes `current_script`.
5. Back in the workflow: render, then the **Critic** reviews (open question). Each revision is checked by
   the **Verifier** against before/after renders and a structural scene diff (closed question, skipped
   for free when nothing changed). An approved build can be distilled into skills by `workflow/learning`
   (four admission gates and a repair round).
6. `finalize` writes `final_script.py`, `result.json` and skill usage statistics; `trace.jsonl` holds
   every span.

## Where to extend

| To add... | Write... | Touch the loop? |
|---|---|---|
| a rule ("stop after tool X", "remind the plan every 5 steps") | a `Rail` subclass, passed as `AgentLoop(rails=[...])` | no |
| a context strategy (summaries, retrieval, offloading) | a `Processor`, added to the agent's `ContextEngine` | no |
| a tool | a `Tool` with a permission tier, in the agent's tool list | no |
| an agent | a prompt in `agents/prompts/` and a config function in `agents/` | no |
| a phase (e.g. planning) | a `_Build` method that updates `BuildState` and saves a checkpoint | no |
| an external interface | a module in `protocol/` on top of `ToolRegistry` | no |

## Testing

`tests/fakes.py` provides a scripted LLM (replays fixed replies, records what the model saw) and a fake
Blender session. The unit tests run the whole pipeline on them in seconds, with no network.
`tests/integration/` runs against a real headless Blender and is skipped when Blender is missing.
