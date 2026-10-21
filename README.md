# BlendHarness

**A self-evolving multi-agent harness for Blender: it turns natural language into editable 3D scenes by writing Blender Python, on top of an agent harness built from scratch.**

[中文说明](README.zh-CN.md)

![Generated examples: a village (test task, built with learned skills), a kitchen corner and a bar stool. Three orbit views and one top view each.](docs/examples.png)

Instead of generating meshes directly, a **Coder** agent writes and runs `bpy` scripts in a sandboxed headless Blender, a vision **Critic** reviews the renders, and a **Verifier** checks every requested fix against before/after renders and a structural scene diff. Approved builds can be distilled into a **self-evolving skill library** of verified, reusable modeling functions. The output is a plain Python script, so every object stays editable.

The original idea comes from the [LL3M paper](https://arxiv.org/abs/2508.08228). This repository contains no LL3M code: the harness, sandbox, agents, memory and evaluation were written from scratch and redesigned around measurements.

## Highlights

- **Own agent harness.** A tool-calling agent loop (the model decides, the harness executes) that keeps only mechanism. Policies live in pluggable **rails**: truncation handling, harness-verified completion, repeated-error stop and cost budget, consulted at fixed events by priority.
- **Process-isolated Blender sandbox with a static guard.** One long-lived headless Blender per task, a reset to an empty scene before every run, and a watchdog for hangs and crashes. Model code is checked with `ast` before it runs (no file system, process or network access). Calibrated on 232 past scripts, including a real run where the Coder walked the host disk to read its own logs.
- **Context engine.** A pipeline of processors builds what the model sees from the full history: stale scripts become placeholders, the current script is pinned, and a budget guard is the last line of defense. Token estimates are calibrated on our own traces (2.3 chars/token for code-heavy context, not 4).
- **Tools with permission tiers.** One `ToolRegistry` (read / write / execute) backs both the agent loop and an **MCP server**, so Claude Desktop, Claude Code or Cursor can use the same sandbox.
- **Checkpoints.** Every phase ends at a safe point with an atomic JSON checkpoint: resume an interrupted build, or fork a new one from any phase with another config.
- **Two-step visual review.** The **Critic** answers an open question ("what is wrong?") and the **Verifier** a closed one ("was each issue fixed, and did anything regress?"). Cheap programmatic checks always run before vision models.
- **Self-evolving skill library.** A librarian distills approved builds into reusable functions, admitted only through four gates (static, dedup, sandbox, structure plus vision) with one repair round. Skills are listed in the prompt and loaded on demand.
- **Tracing and evaluation.** Every LLM call, tool call and agent run is a span with tokens, cost and latency. A resumable, crash-isolated eval runner interleaves variants for fair comparison.
- **Trace-based evaluation and gated self-evolution.** Every past build is rebuilt from its trace into process metrics and a root-cause failure taxonomy. A Reflector turns failure cases into lessons (semantic memory), which are only adopted if a pre-registered A/B test on unseen tasks, with a placebo control, shows they help. So far they did not, and the gate kept them out (see Results).
- **Tested.** 201 tests in about 6 s: unit tests on a scripted fake LLM and fake Blender, layering rules, and integration tests on a real Blender.

## Architecture

```mermaid
flowchart TB
    subgraph EVAL[eval/]
        RUN[run_eval.py: tasks x variants x repeats] --> REP[report.py / compare.py]
        AN[analyze.py] --> ANL[analysis/: trajectories, metrics, failure taxonomy]
    end
    subgraph WF[workflow/]
        C0[Coder builds] --> CR[Critic reviews renders]
        CR -->|issues| C1[Coder revises]
        C1 --> V[Verifier: before/after + scene diff]
        V -->|unresolved only| C1
        CR -->|approved| L[learning/: distil + admit skills]
        V -->|passed| L
        CK[(checkpoints after every phase)]
    end
    subgraph AG[agents/]
        CODER[coder: AgentLoop config]
        SINGLE[critic / verifier / librarian]
        PR[prompts/*.md]
    end
    subgraph TL[tools/ + memory/ + sandbox/]
        BT[BlenderTools: execute / edit / view / inspect / render]
        ST[SkillTools: index / load_skill]
        MEM[memory/: skills + lessons]
        SB[sandbox: guard + session <-> server in Blender]
    end
    subgraph CORE[core/]
        LOOP[runtime: AgentLoop]
        RAILS[rails: truncation / completion / stuck / budget]
        CTX[context_engine: processors + budget guard]
        TOOLS[tools: Tool + ToolRegistry]
        LLM[llm: streaming client, retries, cost]
        SES[session: Checkpointer]
        TR[tracing: span tree]
    end
    MCP[protocol/: MCP server] --> TOOLS
    RUN --> WF
    WF --> AG
    CODER --> BT & ST
    ST --> MEM
    BT --> SB
    AG --> CORE
    LOOP --> RAILS & CTX & TOOLS & LLM
```

| Layer | Folder (`blendharness/`) | Responsibility |
|---|---|---|
| Core | `core/` | Generic mechanism, nothing Blender-specific: `runtime` (agent loop), `rails`, `context_engine`, `tools` (Tool, ToolRegistry), `llm`, `session` (checkpoints), `tracing` |
| Sandbox | `sandbox/` | `server.py` runs **inside** Blender, `session.py` drives it with a watchdog, `guard.py` checks code statically |
| Memory | `memory/` | `skills/`: `SkillLibrary`, one folder per skill (`SKILL.md` + `skill.py` + `stats.json`); `lessons/`: `LessonLibrary`, rules learned from failures |
| Analysis | `analysis/` | Rebuild builds from `trace.jsonl` + `result.json` (coder runs, tool calls, the life of each issue across rounds), process metrics, root-cause failure taxonomy |
| Tools | `tools/` | `BlenderTools` (script state, execute / edit / view / inspect), `scene.py`, `render.py`, `SkillTools` |
| Agents | `agents/` | Configurations: Coder (prompt + tools + context pipeline + completion check), Critic, Verifier, Librarian; prompts in `prompts/*.md` |
| Workflow | `workflow/` | `run_build`: build / critique / revise / verify / learn as checkpointed phases; `learning/`: skill admission gates and learning loop, lesson gates |
| Protocol | `protocol/` | `blendharness-mcp`: the tools as an MCP server |

Layering is one-way (`core` ← `sandbox`, `memory` ← `tools` ← `agents` ← `workflow`; `analysis` uses only `core`; `protocol` uses `tools`) and enforced by `tests/unit/test_architecture.py`. See [`docs/architecture.md`](docs/architecture.md).

Design principles: **mechanism vs. policy** (the loop runs, rails decide), **framework vs. configuration** (one loop, agents are configs), **dependency inversion** (`BlenderTools` only knows a generic `preamble_provider`, not skills), **cheap before expensive** (deterministic checks before vision models), and **fail-safe** (critics and gates reject when in doubt).

## Results

All numbers come from the eval harness and were measured before the October 2026 restructuring (commit `e46c5ab`); the restructuring kept behaviour identical, checked by unchanged tests, character-identical prompts and a differential test of the context policy. The coder is Claude Sonnet with `reasoning_effort: low`, and the critic, verifier and skill review use Claude Haiku. Each condition ran **twice**, so treat small differences as noise.

| Experiment | Setup | Finding |
|---|---|---|
| `m3_vs_baseline` | 6 tasks x 2 repeats, full rewrites + full history vs. edit tool + compaction (v1) | Same pass rate (75%) and −13% cost per task. **Revision rounds got more expensive** (median $0.446 vs $0.274): compaction hid the script being edited (17 re-reads), and many small edits meant many round trips. A single-run "−73%" did not replicate. |
| `m3v2` | Pinned current script + batched all-or-nothing edits, 3 hardest tasks x 2 | **6/6 passed** (baseline 4/6), **−14% cost per task**, 0 re-reads. 85–92% of revision output tokens are hidden thinking, so the remaining cost lever is reasoning control, not context. |
| `skills_lc` | Learning curve: train on 5 tasks, then test on 5 unseen tasks that share components, with vs. without the library | Library grew to 10 verified skills (the repair round rescued 8 rejected candidates). Test pass rate was 6/10 with skills vs 8/10 without, **but all 4 failures were unrelated to skills**. Only **4/10 runs adopted a skill**. Where skills were used (village), 2/2 passed in the first round at −34% cost. Bottleneck: coarse, whole-object skills transfer poorly. |

### Self-evolution from failures, gated by evaluation (October 2026)

| Step | Setup | Finding |
|---|---|---|
| Trajectory analysis | 69 past builds, 79 Critic issues labelled by root cause | Spatial problems are **72%** of all issues. Contact problems (floating, interpenetration) are fixed 73–85% of the time by the Critic → Coder → Verifier loop; **relational** ones (placement, proportion) only **43–45%**, and they are the largest share of problems left in failed builds. Code errors are not the bottleneck: 9% of executions fail and 89% recover within the same run. |
| Lessons | A Reflector reads the 23 relational cases (problem, suggested fix, verifier evidence) | 9 lessons, e.g. "derive a part's position from the bounding box of the part it rests on", "fix placement through location/rotation, not dimensions". Gates: generality (cited cases from ≥ 2 tasks, checked by us), dedup. |
| `lessons_ab` | 8 unseen tasks avoiding training objects, with vs. without lessons, x 2 | Pre-registered primary metric (relational issues in round 0): no result, **floor effect** (2 vs 1 such issues in 16 builds each). Exploratory: −24% cost, −20% coder steps. |
| `lessons_confirm` | Pre-registered confirmation: with / without / **placebo** (generic advice of the same length) x 2 | **The cost effect did not replicate** (with − without: −$0.007, 95% CI [−0.12, +0.11]). The control arm's cost moved 34% between nights, more than the effect being measured. The lessons are therefore **not enabled by default**: the evaluation gate rejected this evolution step. |

Takeaways: an exploratory win needs a pre-registered confirmation and a placebo arm (the second time in this project a single-experiment effect failed to replicate); the test distribution must contain the failures being targeted (relational errors cluster in multi-object scenes, which motivates a dedicated spatial-layout project); and the API relay itself is an experimental variable (it silently blocked some prompts, and the blocking changed over time).

### Skill library

The skill library learned in `skills_lc` is in [`skills/`](skills/):

![Skill library: 10 skills rendered with default parameters](docs/skill_library.png)

Lessons that shaped the design, each found through traces or evals:
- "Runs" is not "correct": 3 of the first 9 extracted skills executed fine but were broken (parts sunk or overlapping). A free structural check (grounded and connected) catches them.
- Reasoning models over-think. Default thinking used 12k–32k tokens before the first tool call. `reasoning_effort: low` cut it to about 1.7k, and the same flag *turns thinking on* for Haiku, so effort is set per agent.
- The relay closed some streams silently after two tokens. Streams without `finish_reason` are now treated as incomplete and retried.

## Getting started

**Requirements:** macOS or Linux, [Blender 4.4.3](https://download.blender.org/release/Blender4.4/), Python 3.12, and an OpenAI-compatible API (the experiments used a relay for Claude and the DeepSeek API).

```bash
conda create -n blender-agent python=3.12 -y && conda activate blender-agent
pip install -e ".[dev,mcp]"    # the blendharness package, pytest and the MCP SDK
cp .env.example .env        # fill in OPENAI_BASE_URL / OPENAI_API_KEY (and DEEPSEEK_* if used)
# Blender path defaults to /Applications/Blender.app/Contents/MacOS/Blender; override with BLENDER_PATH
```

Models are routed by name prefix: `deepseek*` goes to `DEEPSEEK_*`, and everything else goes to `OPENAI_*`. Set prices in `config/prices.json` (USD per million tokens) so costs and budgets are computed.

```bash
python scripts/smoke_llm.py claude-haiku-4-5     # API, function calling, error tracing
python scripts/smoke_blender.py                  # sandbox: reset, errors, watchdog, render

# Build one scene (Coder -> Critic -> revise -> Verifier)
python scripts/run_build.py "Create a wooden dining chair with four legs and a slatted backrest" \
    --model claude-sonnet-4-6 --critic-model claude-haiku-4-5 --rounds 2 --effort low

# Resume an interrupted build, or fork a new one from a phase with another config
python scripts/run_build.py --resume runs/<run_id>
python scripts/run_build.py --fork-from runs/<run_id> --at round0 --rounds 3

# Evaluate
python eval/run_eval.py --exp my_exp --variants baseline m3 --repeats 2 --workers 3
python eval/report.py --exp my_exp
python eval/compare.py --exp my_exp --a baseline --b m3        # paired A/B with bootstrap CIs

# Trace analysis and self-evolution from failures
python eval/analyze.py                  # metrics + failure taxonomy over all past builds -> runs/analysis/
python scripts/reflect.py --dry-run     # the failure cases the Reflector would read
python scripts/reflect.py               # distil lessons into lessons/ (use with BuildConfig(lessons_dir=...))

# Tests (no API calls; the integration tests are skipped without Blender)
python -m pytest

# Serve the tools to MCP clients (Claude Desktop, Claude Code, Cursor): see docs/mcp.md
blendharness-mcp
```

Each run writes `runs/<run_id>/`, which contains `trace.jsonl` (span summaries), `payloads/` (full model requests and replies), `renders/`, `checkpoints/` (one JSON per finished phase), `final_script.py` (standalone, including skill code), `result.json` and `blender.log`.

## Repository layout

```
blendharness/
  core/        runtime/  rails/  context_engine/  tools/  llm/  session/  tracing/
  sandbox/     server.py  session.py  guard.py
  memory/      skills/library.py  lessons/library.py
  analysis/    trajectory.py  metrics.py  taxonomy.py
  tools/       blender_tools.py  scene.py  render.py  skill_tools.py
  agents/      coder.py  critic.py  verifier.py  librarian.py  reflector.py  prompts/*.md
  workflow/    build.py  state.py  learning/admission.py  learning/learn.py  learning/lessons.py
  protocol/    mcp_server.py
eval/          run_eval.py  report.py  compare.py  analyze.py  tasks*.json  placebo_lessons/
scripts/       run_build.py  reflect.py  relearn.py  recost.py  smoke_*.py  demo_loop.py  run_coder.py
tests/         unit/  integration/  fakes.py
skills/        learned skill library
lessons/       learned lessons (not enabled by default: they did not pass the A/B gate)
config/        prices.json
docs/          architecture.md  mcp.md  roadmap.md  images
```

## Limitations and roadmap

- **Small samples.** There are two repeats per condition, and the variance between identical runs is large. Key experiments should be repeated 3–5 times.
- **Lessons not confirmed.** Neither the targeted relational errors nor the cost were measurably reduced on single-object test tasks; the targeted errors are rare there. A fair test needs multi-object scenes, where relational errors actually occur.
- **Skill adoption.** Skills are too coarse to transfer. Next: distil composable parts (leg sets, drawer stacks, handles, roofs) and have the harness retrieve and recommend the top-k skills per request.
- **Cost.** Most revision cost is hidden reasoning. Next: stage-based model routing (cheaper models or lower effort for revisions) and a failure-driven cascade, compared against all-cheap and all-strong.
- **Sandbox scope.** Process isolation plus a static guard catches the realistic cases, but static checks can be obfuscated around; a public service would need containers.
- **Rendering.** Colors are slightly washed out, and specular highlights on liquids can make vision models hallucinate objects.

More design notes are in [`docs/roadmap.md`](docs/roadmap.md).
