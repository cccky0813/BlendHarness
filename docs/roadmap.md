# Roadmap & design decisions

## Done
- Harness: span tracing; streaming multi-provider LLM client (retries, cost, incomplete-stream detection); agent loop
  (error-as-observation, budgets, truncation handling, completion checks, context-policy hook); context building blocks
- Process-isolated Blender sandbox (long-lived headless Blender, reset per run, watchdog)
- Agents: Coder (AgentLoop), Critic, Verifier, Librarian (single calls); thinking effort per agent
- M3 context management v2: pinned current script + batched transactional edits (eval: 6/6 hard tasks, -14% cost)
- Eval harness: task sets, resumable crash-isolated parallel runner, interleaved variants, trace-based reports
- M4 skill library: storage + progressive disclosure (SkillTools), librarian, 4 admission gates + repair round,
  learning-curve experiment (finding: adoption 4/10 is the bottleneck; skills too coarse)

## Next (prioritized)
1. M4: distil fine-grained composable parts; harness retrieves and recommends top-k skills per request; re-test 4 tasks x 3
2. Stage-based model routing / reasoning control for revision rounds; failure-driven cascade vs all-cheap / all-strong
3. Phase-level checkpoints (resume / fork from any stage)
4. Polish: render colors, liquid highlights, post-verify critic pass for regressions
5. Larger samples (3-5 repeats) for key experiments
6. (Low priority) error-experience memory; RAG only if API-knowledge errors become significant

## Design decisions to keep
### Phase-level checkpoint (orchestration layer)
- State is plain JSON (task, subtasks + status, retrieved docs, current_script, config). No client objects in state.
- Save only at safe points (a phase / subtask fully finished). Atomic write: `state.json.tmp` → rename.
- Every phase checks state first and skips work already done → resume from any point gives the same result.
- Blender scene is not saved: rebuilt by re-running `current_script` (full-script semantics).
- Fork from a checkpoint: plan once, run several Coder variants on the same plan → cheaper and fairer ablations.

### RAG over Blender docs: deferred (data-driven)
- 2026-10-15 runs: 14 executions, 2 failures, only 1 caused by outdated API knowledge (`use_auto_smooth`).
  All Critic findings were spatial/geometric (floating parts, scale, buried objects) — RAG doesn't help there.
- Long-tail API errors → M4 experience memory (error signature → fix) instead of a doc pipeline.
- Revisit in M5: if API-knowledge errors are a significant share of failures, add an on-demand
  `search_docs` tool (only after an error) and prove it with a with/without ablation.

### Thinking control (measured 2026-10-15)
- Reasoning models over-think before acting: DeepSeek default high → 8k tokens of thinking, truncated;
  Claude Sonnet via relay default → 12k–32k thinking tokens before a single tool call ($0.48 wasted once).
- Relay ignores Anthropic-style `thinking: {disabled | budget_tokens}`; `reasoning_effort: "low"` works:
  game scene in one call, ~1.7k thinking + ~2.3k code tokens.
- Harness guards: finish_reason=length is not "done" (nudge, then `truncated`); reasoning tokens traced.

### Model routing (first data, n=1 each — directional only)
- Game scene: Sonnet better geometry in round 0; Haiku needed one critic round. Excluding wasted thinking,
  cost was similar (~$0.10 vs $0.12) because Haiku re-reads the whole script every round.
- Plan: failure-driven cascade (cheap model first, escalate on critic rejection / stuck), evaluated against
  all-cheap and all-strong in M5.

### Eval-layer resume
- One result file per task; on restart skip tasks that already have a result.

### M4: self-evolving skill library (as built)
- Skills stored in Agent-Skills style (name, description, instructions, code); only the skill index sits in context, `load_skill(name)` loads the body (progressive disclosure).
- Experience memory: error signature → successful fix; hybrid BM25 + embedding retrieval; SQLite + numpy.
- Evidence: learning curve (tasks seen → success rate / retries / cost) + held-out test set. Not RSI: no weight updates.
