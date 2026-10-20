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

from blendharness.agents.coder import make_coder
from blendharness.agents.critic import critique
from blendharness.agents.prompts import load_prompt
from blendharness.agents.verifier import verify
from blendharness.core.llm import LLMClient
from blendharness.core.session import Checkpointer
from blendharness.core.tracing import Tracer
from blendharness.memory.lessons import LessonLibrary
from blendharness.memory.skills import SkillLibrary
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.blender_tools import BlenderTools
from blendharness.tools.scene import diff_snapshots
from blendharness.tools.skill_tools import SkillTools
from blendharness.workflow.learning import learn_from_build
from blendharness.workflow.state import BuildState

REFINE_TEMPLATE_NO_EDIT = load_prompt("refine_full_script")

# With edit tools the script is not repeated here: the context policy pins the current version.
REFINE_TEMPLATE = load_prompt("refine_edit")


# Edit tools but no compaction (ablation): no pinned script in context, so the task must carry it.
REFINE_TEMPLATE_EDIT_NO_PIN = load_prompt("refine_edit_no_pin")


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
    lessons_dir: str | None = None             # RSI: lesson library folder (None = no lessons in the prompt)
    track_lesson_usage: bool = False           # update lesson stats (off by default: A/B tests need a frozen library)


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
              log: Callable[[str], None] = print,
              llm_factory: Callable[..., LLMClient] = LLMClient,
              session_factory: Callable[[Path], BlenderSession] = BlenderSession,
              resume: bool = False,
              fork_from: tuple[str | Path, str] | None = None) -> dict:
    """Run the full pipeline once; writes trace/renders/checkpoints/final_script/result.json into run_dir.

    resume=True       continue an interrupted build in run_dir from its latest checkpoint (its saved config wins)
    fork_from=(d, l)  start a NEW build in run_dir from checkpoint `l` of build `d`, possibly with another
                      config, e.g. same round-0 build, different revision strategy (a cheaper, fairer ablation)
    llm_factory(model, tracer, extra_body=...) and session_factory(log_path) default to the real classes;
    tests inject scripted fakes to run the whole pipeline without network or Blender.
    """
    run_dir = Path(run_dir)
    checkpoints = Checkpointer(run_dir)
    state = checkpoints.load() if resume else None
    if state is not None:
        state = BuildState.from_dict(state)
        log(f"--- resuming {run_dir.name} after checkpoint '{checkpoints.labels()[-1]}'")
    elif fork_from is not None:
        source, label = Path(fork_from[0]), fork_from[1]
        saved = Checkpointer(source).load(label)
        if saved is None:
            raise ValueError(f"no checkpoint {label!r} in {source}; available: {Checkpointer(source).labels()}")
        state = BuildState.from_dict(saved)
        state.config, state.forked_from = asdict(cfg), f"{source}@{label}"
        state.rounds_finished = state.approved or not state.current_script   # more rounds may be allowed now
        checkpoints.save("fork", state.to_dict())
        log(f"--- forked from {state.forked_from}")
    else:
        state = BuildState(prompt=prompt, config=asdict(cfg))

    tracer = Tracer(run_dir, run_id=run_dir.name, resume=resume)
    with session_factory(run_dir / "blender.log") as session:
        build = _Build(BuildConfig(**state.config), state, run_dir, tracer, session, checkpoints, log, llm_factory)
        with tracer.span("run", "build", prompt=state.prompt, config=state.config,
                         resumed=resume, forked_from=state.forked_from or None):
            if state.completed_rounds < 0:
                build.round0()
            while not state.rounds_finished:
                build.revise(state.completed_rounds + 1)
        if state.learned is None:
            build.learn()
        return build.finalize()


class _Build:
    """The live side of one build: agents, tools and sandbox, plus one method per phase.
    Each phase reads and updates `state` and ends with a checkpoint (a safe point)."""

    def __init__(self, cfg: BuildConfig, state: BuildState, run_dir: Path, tracer: Tracer, session,
                 checkpoints: Checkpointer, log: Callable[[str], None], llm_factory) -> None:
        self.cfg, self.state, self.run_dir, self.tracer, self.session = cfg, state, run_dir, tracer, session
        self.checkpoints, self.log, self.llm_factory = checkpoints, log, llm_factory
        self.t0 = time.perf_counter()
        self.elapsed_before, self.restarts_before = state.elapsed_s, state.sandbox_restarts

        self.skills = SkillLibrary(cfg.skills_dir) if cfg.skills_dir else None
        self.skill_tools = SkillTools(self.skills) if self.skills is not None else None
        self.blender = BlenderTools(session, run_dir / "renders",
                                    preamble_provider=self.skill_tools.preamble if self.skill_tools else None)
        # Restore the tool world from the state (no-ops for a fresh build).
        self.blender.current_script = state.current_script
        self.blender.history = state.exec_history
        if self.skill_tools:
            self.skill_tools.loaded = state.loaded_skills
        # Lessons are fixed per build: the ids chosen at the start are kept in the state, so a resumed build
        # sees exactly the same lessons even if the library has changed since.
        self.lessons = LessonLibrary(cfg.lessons_dir) if cfg.lessons_dir else None
        if self.lessons is not None and state.completed_rounds < 0 and not state.lesson_ids:
            state.lesson_ids = [x.id for x in self.lessons.all()]
        lesson_text = "\n".join(f"- {x.text}" for x in (self.lessons.all() if self.lessons else [])
                                if x.id in state.lesson_ids)
        self.coder = make_coder(llm_factory(cfg.coder_model, tracer, extra_body=effort_body(cfg.coder_model, cfg.coder_effort)),
                                self.blender, skill_tools=self.skill_tools, max_cost_usd=cfg.coder_max_cost_usd,
                                compact_context=cfg.compact_context, edit_tools=cfg.edit_tools, lessons=lesson_text)
        self.critic_llm = llm_factory(cfg.critic_model, tracer, extra_body=effort_body(cfg.critic_model, cfg.critic_effort))
        self.verifier_llm = self.critic_llm  # same model by default; still a separate role with its own prompt
        self.refine = refine_template(cfg)

    # ---------- phases ----------
    def round0(self) -> None:
        """Build from the prompt, then an open-ended critique."""
        s, blender = self.state, self.blender
        result = self.coder.run(s.prompt)
        if blender.current_script:
            renders = blender.render("round0", num_views=3)
            verdict = critique(self.critic_llm, s.prompt, renders, blender.inspect_scene().content)
            s.approved, s.open_issues, s.feedback, s.last_renders = verdict.approved, verdict.issues, verdict.as_text(), renders
            s.rounds_log.append({"round": 0, "coder_status": result.status, "coder_steps": result.steps,
                                 "renders": renders, "critic_approved": verdict.approved,
                                 "issues": s.open_issues, "critic_parse_error": verdict.parse_error})
            self.log(f"--- round 0: coder={result.status} ({result.steps} steps) | critic: "
                     f"{'APPROVED' if s.approved else str(len(s.open_issues)) + ' issues'}")
        else:
            s.rounds_log.append({"round": 0, "coder_status": result.status, "note": "no working script"})
            self.log(f"--- round 0: coder={result.status}, no working script ({result.reason})")
        s.completed_rounds = 0
        s.rounds_finished = s.approved or not blender.current_script or self.cfg.rounds < 1
        self.save("round0")

    def revise(self, rnd: int) -> None:
        """Coder fixes the open issues, Verifier checks each one (closed question)."""
        s, blender = self.state, self.blender
        before_snapshot, before_renders = blender.snapshot(), s.last_renders
        result = self.coder.run(self.refine.format(request=s.prompt, script=blender.current_script,
                                                   issues=s.feedback))  # {script} unused by the edit template
        renders = blender.render(f"round{rnd}", num_views=3)
        diff = diff_snapshots(before_snapshot, blender.snapshot())
        check = verify(self.verifier_llm, s.prompt, s.open_issues, before_renders, renders, diff)
        s.approved, s.last_renders = check.passed, renders
        s.rounds_log.append({"round": rnd, "coder_status": result.status, "coder_steps": result.steps,
                             "renders": renders, "diff": diff, "verified": check.passed,
                             "verdicts": check.issues, "regressions": check.regressions,
                             "verifier_skipped_llm": check.skipped_llm})
        self.log(f"--- round {rnd}: coder={result.status} ({result.steps} steps) | verifier: "
                 f"{'PASSED' if check.passed else 'FAILED'}"
                 f"{' (scene unchanged, no LLM call)' if check.skipped_llm else ''}")
        if not s.approved:
            # Next revision only hears what is still broken (fixed issues are dropped).
            s.feedback = check.unresolved_text(s.open_issues)
            s.open_issues = [s.open_issues[v["index"] - 1] for v in check.issues
                             if v.get("verdict") != "FIXED" and 0 < v.get("index", 0) <= len(s.open_issues)]
            s.open_issues += [{"problem": f"Regression: {r}", "fix": "Restore what was correct before."}
                              for r in check.regressions]
        else:
            s.open_issues = []
        s.completed_rounds = rnd
        s.rounds_finished = s.approved or rnd >= self.cfg.rounds
        self.save(f"round{rnd}")

    def learn(self) -> None:
        """Self-evolution: turn an approved build into reusable skills (only verified code is admitted)."""
        s, cfg = self.state, self.cfg
        s.learned = {"added": [], "rejected": [], "proposed": 0}
        if s.approved and self.skills is not None and cfg.learn_skills:
            lib_model = cfg.librarian_model or cfg.coder_model
            s.learned = learn_from_build(
                self.llm_factory(lib_model, self.tracer, extra_body=effort_body(lib_model, cfg.coder_effort)),
                self.critic_llm, s.prompt, self.blender.full_script(), self.skills, self.session,
                source_task=self.run_dir.name, renders_dir=self.run_dir / "renders" / "skill_checks", log=self.log)
            self.save("learned")

    def finalize(self) -> dict:
        s, blender, skills, skill_tools = self.state, self.blender, self.skills, self.skill_tools
        (self.run_dir / "final_script.py").write_text(blender.full_script(), encoding="utf-8")  # standalone, incl. skills
        for name in (skill_tools.loaded if skill_tools and self.cfg.track_skill_usage else []):  # quality signal
            skills.record_use(name, success=s.approved)
        if self.lessons is not None and self.cfg.track_lesson_usage and s.lesson_ids:
            self.lessons.record_use(s.lesson_ids, success=s.approved)
        total = self.tracer.summary()
        record = {
            "prompt": s.prompt, "config": s.config, "approved": s.approved,
            "status": "approved" if s.approved else ("no_script" if not blender.current_script else "not_approved"),
            "rounds": s.rounds_log, "n_rounds": len(s.rounds_log), "open_issues": s.open_issues,
            "wall_time_s": round(self._elapsed(), 1),
            "executions": len(blender.history),
            "failed_executions": sum(not h["ok"] for h in blender.history),
            "exec_errors": [h["error"].strip().splitlines()[0][:200] for h in blender.history
                            if not h["ok"] and h.get("error")],
            "sandbox_restarts": self.restarts_before + self.session.restarts,
            "skills_available": skills.names() if skills else [],
            "skills_loaded": list(skill_tools.loaded) if skill_tools else [],
            "skills_learned": s.learned,
            "lessons_used": list(s.lesson_ids),
            "checkpoints": self.checkpoints.labels(),
            "forked_from": s.forked_from or None,
            "llm_total": total["total"], "llm_per_agent": total["per_agent"],
        }
        (self.run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    # ---------- checkpoint ----------
    def save(self, label: str) -> None:
        """A safe point: copy the tool world into the state and write it atomically."""
        s = self.state
        s.current_script = self.blender.current_script
        s.exec_history = self.blender.history
        s.loaded_skills = list(self.skill_tools.loaded) if self.skill_tools else []
        s.elapsed_s = round(self._elapsed(), 1)
        s.sandbox_restarts = self.restarts_before + self.session.restarts
        self.checkpoints.save(label, s.to_dict())

    def _elapsed(self) -> float:
        return self.elapsed_before + time.perf_counter() - self.t0
