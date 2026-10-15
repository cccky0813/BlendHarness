"""Orchestration: Coder + Critic + Verifier on one prompt, as a reusable function.

    coder builds -> render -> critic (open question: what is wrong?)
      -> coder revises -> verifier (closed question: was each issue fixed? regressions?)
      -> unresolved issues only -> coder revises -> verifier ... (max N revision rounds)

Used by scripts/run_build.py (one prompt from the command line) and eval/run_eval.py (a task set).
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from agents.coder import make_coder
from agents.critic import critique
from agents.librarian import propose_skills
from agents.verifier import verify
from harness.llm import LLMClient
from harness.tracing import Tracer
from memory.skill_admission import admit
from memory.skill_library import SkillLibrary
from tools.blender_session import BlenderSession
from tools.blender_tools import BlenderTools, diff_snapshots
from tools.skill_tools import SkillTools

REFINE_TEMPLATE_NO_EDIT = """Revise an existing Blender model.

Original request:
{request}

Current script (it runs without errors):
```python
{script}
```

A visual review of renders of this model found these problems:
{issues}

Fix these problems, keeping everything that is already good. Run the complete revised script with
execute_bpy. Then check with inspect_scene, and only then reply with a short summary."""

# With edit tools the script is not repeated here: the context policy pins the current version.
REFINE_TEMPLATE = """Revise an existing Blender model.

Original request:
{request}

The current working script runs without errors and is shown at the end of your context.

A visual review of renders of this model found these problems:
{issues}

Fix these problems, keeping everything that is already good: make ALL the fixes in ONE edit_script call
(a list of exact old_string -> new_string replacements). Then check with inspect_scene, and only then reply
with a short summary."""


# Edit tools but no compaction (ablation): no pinned script in context, so the task must carry it.
REFINE_TEMPLATE_EDIT_NO_PIN = REFINE_TEMPLATE.replace(
    "The current working script runs without errors and is shown at the end of your context.",
    "Current working script (it runs without errors):\n```python\n{script}\n```")


def refine_template(cfg: "BuildConfig") -> str:
    if not cfg.edit_tools:
        return REFINE_TEMPLATE_NO_EDIT
    return REFINE_TEMPLATE if cfg.compact_context else REFINE_TEMPLATE_EDIT_NO_PIN


@dataclass
class BuildConfig:
    coder_model: str = "claude-sonnet-4-6"
    critic_model: str = "claude-haiku-4-5"     # also used as the verifier model
    rounds: int = 2                            # max revision rounds after the first build
    coder_effort: str = "low"
    critic_effort: str = "default"
    edit_tools: bool = True                    # M3: edit_script / view_script
    compact_context: bool = True               # M3: history compaction
    coder_max_cost_usd: float = 0.5            # per coder run
    skills_dir: str | None = None              # M4: skill library folder (None = no skills)
    learn_skills: bool = False                 # M4: after an approved build, extract + admit new skills
    librarian_model: str | None = None         # default: coder_model
    track_skill_usage: bool = True             # update stats.json (off = frozen library for a fair test)


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


def run_build(prompt: str, cfg: BuildConfig, run_dir: str | Path,
              log: Callable[[str], None] = print) -> dict:
    """Run the full pipeline once; writes trace/renders/final_script/result.json into run_dir."""
    run_dir = Path(run_dir)
    tracer = Tracer(run_dir, run_id=run_dir.name)
    t0 = time.perf_counter()
    rounds_log: list[dict] = []
    approved = False
    open_issues: list[dict] = []

    with BlenderSession(run_dir / "blender.log") as session:
        skills = SkillLibrary(cfg.skills_dir) if cfg.skills_dir else None
        skill_tools = SkillTools(skills) if skills is not None else None
        blender = BlenderTools(session, run_dir / "renders",
                               preamble_provider=skill_tools.preamble if skill_tools else None)
        coder = make_coder(LLMClient(cfg.coder_model, tracer, extra_body=effort_body(cfg.coder_model, cfg.coder_effort)),
                           blender, skill_tools=skill_tools, max_cost_usd=cfg.coder_max_cost_usd,
                           compact_context=cfg.compact_context, edit_tools=cfg.edit_tools)
        critic_llm = LLMClient(cfg.critic_model, tracer, extra_body=effort_body(cfg.critic_model, cfg.critic_effort))
        verifier_llm = critic_llm  # same model by default; still a separate role with its own prompt
        refine = refine_template(cfg)

        with tracer.span("run", "build", prompt=prompt, config=asdict(cfg)):
            # Round 0: build from the prompt, then an open-ended critique.
            result = coder.run(prompt)
            if blender.current_script:
                renders = blender.render("round0", num_views=3)
                verdict = critique(critic_llm, prompt, renders, blender.inspect_scene().content)
                approved, open_issues = verdict.approved, verdict.issues
                rounds_log.append({"round": 0, "coder_status": result.status, "coder_steps": result.steps,
                                   "renders": renders, "critic_approved": verdict.approved,
                                   "issues": open_issues, "critic_parse_error": verdict.parse_error})
                log(f"--- round 0: coder={result.status} ({result.steps} steps) | critic: "
                    f"{'APPROVED' if approved else str(len(open_issues)) + ' issues'}")
                feedback = verdict.as_text()

                # Revision rounds: Coder fixes, Verifier checks each issue (closed question).
                for rnd in range(1, cfg.rounds + 1):
                    if approved:
                        break
                    before_snapshot, before_renders = blender.snapshot(), renders
                    result = coder.run(refine.format(request=prompt, script=blender.current_script, issues=feedback))  # {script} unused by the edit template
                    renders = blender.render(f"round{rnd}", num_views=3)
                    diff = diff_snapshots(before_snapshot, blender.snapshot())
                    check = verify(verifier_llm, prompt, open_issues, before_renders, renders, diff)
                    approved = check.passed
                    rounds_log.append({"round": rnd, "coder_status": result.status, "coder_steps": result.steps,
                                       "renders": renders, "diff": diff, "verified": check.passed,
                                       "verdicts": check.issues, "regressions": check.regressions,
                                       "verifier_skipped_llm": check.skipped_llm})
                    log(f"--- round {rnd}: coder={result.status} ({result.steps} steps) | verifier: "
                        f"{'PASSED' if check.passed else 'FAILED'}"
                        f"{' (scene unchanged, no LLM call)' if check.skipped_llm else ''}")
                    if not approved:
                        # Next revision only hears what is still broken (fixed issues are dropped).
                        feedback = check.unresolved_text(open_issues)
                        open_issues = [open_issues[v["index"] - 1] for v in check.issues
                                       if v.get("verdict") != "FIXED" and 0 < v.get("index", 0) <= len(open_issues)]
                        open_issues += [{"problem": f"Regression: {r}", "fix": "Restore what was correct before."}
                                        for r in check.regressions]
                    else:
                        open_issues = []
            else:
                rounds_log.append({"round": 0, "coder_status": result.status, "note": "no working script"})
                log(f"--- round 0: coder={result.status}, no working script ({result.reason})")

        learned = {"added": [], "rejected": [], "proposed": 0}
        if approved and skills is not None and cfg.learn_skills:
            # Self-evolution: turn this approved build into reusable skills (only verified code is admitted).
            lib_model = cfg.librarian_model or cfg.coder_model
            proposal = propose_skills(LLMClient(lib_model, tracer, extra_body=effort_body(lib_model, cfg.coder_effort)),
                                      prompt, blender.full_script(), skills.index_text(), source_task=run_dir.name)
            admission = admit(proposal.skills, skills, session, critic_llm=critic_llm,
                              renders_dir=run_dir / "renders" / "skill_checks")
            learned = {"added": admission.admitted, "rejected": admission.rejected,
                       "proposed": len(proposal.skills), "parse_error": proposal.parse_error}
            log(f"--- librarian: proposed {len(proposal.skills)}, added {admission.admitted}, "
                f"rejected {[r['name'] + ' (' + r['gate'] + ')' for r in admission.rejected]}")

        (run_dir / "final_script.py").write_text(blender.full_script(), encoding="utf-8")  # standalone, incl. skills
        for name in (skill_tools.loaded if skill_tools and cfg.track_skill_usage else []):  # quality signal
            skills.record_use(name, success=approved)
        total = tracer.summary()
        record = {
            "prompt": prompt, "config": asdict(cfg), "approved": approved,
            "status": "approved" if approved else ("no_script" if not blender.current_script else "not_approved"),
            "rounds": rounds_log, "n_rounds": len(rounds_log), "open_issues": open_issues,
            "wall_time_s": round(time.perf_counter() - t0, 1),
            "executions": len(blender.history),
            "failed_executions": sum(not h["ok"] for h in blender.history),
            "exec_errors": [h["error"].strip().splitlines()[0][:200] for h in blender.history
                            if not h["ok"] and h.get("error")],
            "sandbox_restarts": session.restarts,
            "skills_available": skills.names() if skills else [],
            "skills_loaded": list(skill_tools.loaded) if skill_tools else [],
            "skills_learned": learned,
            "llm_total": total["total"], "llm_per_agent": total["per_agent"],
        }
        (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record
