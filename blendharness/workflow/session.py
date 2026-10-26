"""Multi-turn sessions: a user keeps asking for changes to the same scene, turn after turn.

    session dir/
      checkpoints/       t01_done.json ... (session state after each turn: resume or fork from any)
                         memory_t01.db ... (structured memory after each turn, same purpose)
      turns/t01/         one run_build per turn: trace, renders, checkpoints, scene_before/after.json, result.json
      turns/t01/plan/    the ledger keeper's call (structured memory)
      memory.db          structured memory of the turn in progress
      session.json       summary of every turn so far

Each turn is one run_build: turn 1 builds from an empty scene; from turn 2 on, the build STARTS from the previous
turn's working script, so round 0 edits it, and the Critic / Verifier / revision rounds work exactly as in a single
build. What the Coder is told about earlier turns is the `memory` strategy (docs/multiturn_design.md 5.7):

    history     (M0) every earlier request verbatim
    structured  (M2) the requirement ledger + the scene registry + one line per earlier turn, from memory/session;
                the ledger keeper updates the ledger and resolves references before the edit
    retrieval   (M3) M2, and the script is shown as an outline plus the k sections retrieved for the request
                (BM25 + embeddings + RRF over memory/session/sections); the rest is loaded with view_section.
                Each turn records which sections the edit really touched, so recall@k is measured as it runs

From turn 2 the Coder must declare what it changed (declare_changes); objects that changed without being declared
are recorded as unrequested changes.

The system only ever receives turn k's request as a string, after turn k-1 has finished: it never sees later turns,
and never sees an eval session's expected changes (eval/run_sessions.py keeps those to itself).
"""

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Optional

from blendharness.agents.ledger_keeper import LedgerUpdate, update_ledger
from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient
from blendharness.core.session import Checkpointer
from blendharness.core.tracing import Tracer
from blendharness.memory.session import (SessionMemory, episode_line, registry_rows, render_episodes, render_ledger,
                                         render_registry)
from blendharness.memory.session.retrieval import Embedder, LocalEmbedder, Retriever, recall_at_k
from blendharness.memory.session.sections import split_sections, touched_sections
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.scene import diff_snapshots
from blendharness.workflow.build import BuildConfig, run_build

TURN_EDIT = load_prompt("turn_edit")
MEMORY_STRATEGIES = ("history", "structured", "retrieval")      # M0, M2, M3


@dataclass
class SessionConfig:
    build: BuildConfig = field(default_factory=BuildConfig)
    memory: str = "history"               # what the Coder is told about earlier turns
    ledger_model: str = "claude-haiku-4-5"
    retrieval_k: int = 3                  # retrieval: sections shown in full
    retrieval_mode: str = "hybrid"        # bm25 | vector | hybrid

    def to_dict(self) -> dict:
        return {**asdict(self), "build": asdict(self.build)}

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        return cls(**{**d, "build": BuildConfig(**d["build"])})


@dataclass
class SessionState:
    config: dict
    turns: list[dict] = field(default_factory=list)   # one record per finished turn
    current_script: str = ""                          # the scene after the last finished turn
    forked_from: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        return cls(**d)


REVIEW_RULE = ("Judge mainly whether the current request was carried out. Check the earlier requirements only for "
               "things this turn broke; do not reject the model for choices made in earlier turns.")


def turn_texts(earlier: list[str], request: str, memory: str) -> tuple[str, str]:
    """(task for the Coder, text the Critic and Verifier judge against) under the `history` strategy.
    Takes only what has happened so far and the new request: there is nothing else to leak."""
    if not earlier:
        return request, request                       # turn 1: an ordinary build
    if memory != "history":
        raise NotImplementedError(f"turn_texts is the history strategy; got {memory!r}")
    history = "\n".join(f"{i}. {r}" for i, r in enumerate(earlier, 1))
    turn = len(earlier) + 1
    task = TURN_EDIT.format(context="Earlier requests, oldest first (they still apply unless a later one changed "
                                    f"them):\n{history}", turn=turn, request=request)
    review = (f"Current request (turn {turn}):\n{request}\n\nEarlier requests that still apply unless a later one "
              f"changed them:\n{history}\n\n{REVIEW_RULE}")
    return task, review


def structured_texts(mem: SessionMemory, request: str, turn: int, references: list[dict],
                     new_ids: list[str]) -> tuple[str, str]:
    """(task, review) under the `structured` strategy: ledger + registry + episodes instead of the raw history."""
    if turn == 1:
        return request, request
    ledger = render_ledger([r for r in mem.requirements(active_only=True) if r["id"] not in new_ids])
    refs = "\n".join(f'- "{r["phrase"]}" means: {", ".join(r["objects"])}' for r in references)
    context = (f"Requirements that still apply (from earlier turns, oldest first):\n{ledger or '(none)'}\n\n"
               f"Scene registry (groups of objects; inspect_scene has full detail):\n{render_registry(mem.objects())}\n\n"
               f"What earlier turns did:\n{render_episodes(mem.episodes())}"
               + (f"\n\nIn the new request:\n{refs}" if refs else ""))
    task = TURN_EDIT.format(context=context, turn=turn, request=request)
    review = (f"Current request (turn {turn}):\n{request}\n\nRequirements from earlier turns that still apply:\n"
              f"{ledger or '(none)'}\n\n{REVIEW_RULE}")
    return task, review


def unrequested_changes(before: dict, after: dict, declared: list[dict]) -> list[str]:
    """Objects that were added, removed or changed by the turn but not declared by the Coder."""
    named = {n for d in declared for k in ("added", "modified", "removed") for n in d.get(k, [])}
    diff = diff_snapshots(before, after)
    if "error" in diff:
        return []
    touched = set(diff["added"]) | set(diff["removed"]) | {c["name"] for c in diff["changed"]}
    return sorted(touched - named)


class Session:
    """A live session: start it, then call turn(request) once per user message."""

    def __init__(self, session_dir: str | Path, cfg: SessionConfig, log: Callable[[str], None] = print,
                 llm_factory: Callable[..., LLMClient] = LLMClient,
                 session_factory: Callable[[Path], BlenderSession] = BlenderSession,
                 resume: bool = False, fork_from: Optional[tuple[str | Path, int]] = None,
                 embedder: Optional[Embedder] = None) -> None:
        self.dir = Path(session_dir)
        self._embedder = embedder
        self.log, self.llm_factory, self.session_factory = log, llm_factory, session_factory
        self.checkpoints = Checkpointer(self.dir)
        saved = self.checkpoints.load() if resume else None
        if saved is not None:
            self.state = SessionState.from_dict(saved)
            log(f"--- resuming session {self.dir.name} after turn {len(self.state.turns)}")
        elif fork_from is not None:
            source, k = Path(fork_from[0]), fork_from[1]
            saved = Checkpointer(source).load(f"t{k:02d}_done")
            if saved is None:
                raise ValueError(f"no turn {k} in {source}; available: {Checkpointer(source).labels()}")
            self.state = SessionState.from_dict(saved)
            self.state.config, self.state.forked_from = cfg.to_dict(), f"{source}@t{k:02d}"
            self.checkpoints.save("fork", self.state.to_dict())
            src_mem = source / "checkpoints" / f"memory_t{k:02d}.db"
            if src_mem.exists():                       # the fork starts from the memory of the turn it branches from
                SessionMemory.restore(src_mem, self._memory_copy(k)).close()
            log(f"--- forked from {self.state.forked_from}")
        else:
            self.state = SessionState(config=cfg.to_dict())
        self.cfg = SessionConfig.from_dict(self.state.config)
        if self.cfg.memory not in MEMORY_STRATEGIES:
            raise NotImplementedError(f"memory strategy {self.cfg.memory!r} is not implemented yet")

    @property
    def embedder(self) -> Optional[Embedder]:
        if self._embedder is None and self.cfg.retrieval_mode != "bm25":
            self._embedder = LocalEmbedder()
        return self._embedder

    def _memory_copy(self, k: int) -> Path:
        return self.dir / "checkpoints" / f"memory_t{k:02d}.db"

    def turn(self, request: str) -> dict:
        """Run the next turn to the end (plan, edit, critique, revisions, bookkeeping) and return its record."""
        s, cfg = self.state, self.cfg
        k = len(s.turns) + 1
        turn_dir = self.dir / "turns" / f"t{k:02d}"
        self.log(f"=== turn {k}: {request[:100]}")
        plan, mem, plan_cost = None, None, 0.0
        focus, retrieval = None, None
        if cfg.memory in ("structured", "retrieval"):
            # Restart from the previous turn's copy: redoing an interrupted turn cannot add a requirement twice.
            mem = SessionMemory.restore(self._memory_copy(k - 1) if k > 1 else None, self.dir / "memory.db")
            plan, plan_cost = self._plan(mem, request, k, turn_dir)
            task, review = structured_texts(mem, request, k, plan["references"], plan["added"])
            if cfg.memory == "retrieval" and s.current_script:
                query = " ".join([request] + [o for r in plan["references"] for o in r["objects"]])
                focus = Retriever(self.embedder, mem.db).search(query, split_sections(s.current_script),
                                                                cfg.retrieval_k, cfg.retrieval_mode)
                retrieval = {"query": query, "mode": cfg.retrieval_mode, "focus": focus}
                self.log(f"--- retrieved sections: {focus}")
        else:
            task, review = turn_texts([t["request"] for t in s.turns], request, cfg.memory)
        unfinished = Checkpointer(turn_dir).labels() and not (turn_dir / "result.json").exists()
        build_cfg = replace(cfg.build, declare_changes=k > 1, save_scenes=True,
                            script_view="outline" if focus is not None else cfg.build.script_view)
        record = run_build(task, build_cfg, turn_dir, log=self.log, llm_factory=self.llm_factory,
                           session_factory=self.session_factory, resume=bool(unfinished),
                           initial_script=s.current_script, review=review, focus=focus)
        after_script = (Checkpointer(turn_dir).load() or {}).get("current_script") or s.current_script
        declared = record.get("declared_changes", [])
        scene_after = self._read(turn_dir / "scene_after.json")
        unrequested = unrequested_changes(self._read(turn_dir / "scene_before.json"), scene_after, declared) if k > 1 else []
        entry = {"turn": k, "request": request, "run_dir": str(turn_dir), "approved": record["approved"],
                 "status": record["status"], "n_rounds": record["n_rounds"],
                 "cost_usd": record["llm_total"].get("cost_usd", 0.0) + plan_cost,
                 "declared_changes": declared, "unrequested_changes": unrequested,
                 "script_changed": after_script != s.current_script}
        if s.current_script:                  # which sections the edit really touched: the ground truth for recall@k
            gold = touched_sections(s.current_script, after_script)
            entry["touched_sections"] = gold
            if retrieval is not None:
                entry["retrieval"] = {**retrieval, "recall": recall_at_k(focus, gold, cfg.retrieval_k)}
        if mem is not None:
            rows = registry_rows(scene_after, mem.objects(), k, declared, plan["added"][0] if plan["added"] else None)
            mem.set_objects(rows)
            mem.add_episode(k, request, episode_line(k, request, declared, record["approved"], len(unrequested)),
                            {"declared": declared, "unrequested": unrequested, "approved": record["approved"]})
            mem.save_copy(self._memory_copy(k))
            mem.close()
            entry["ledger"] = {k2: plan[k2] for k2 in ("added", "superseded", "references", "fallback", "problems")}
        s.turns.append(entry)
        s.current_script = after_script
        self.checkpoints.save(f"t{k:02d}_done", s.to_dict())
        self._write_summary()
        return entry

    def _plan(self, mem: SessionMemory, request: str, k: int, turn_dir: Path) -> tuple[dict, float]:
        """Ledger keeper: new requirements, superseded ones, references; applied only after its checks passed."""
        tracer = Tracer(turn_dir / "plan", run_id=f"t{k:02d}_plan")
        active = mem.requirements(active_only=True)
        objects = mem.objects()
        update: LedgerUpdate = update_ledger(
            self.llm_factory(self.cfg.ledger_model, tracer), render_ledger(active), render_registry(objects),
            render_episodes(mem.episodes()), request, {r["id"] for r in active}, {o["name"] for o in objects})
        added = [mem.add_requirement(k, text) for text in update.add]
        for sup in update.supersede:
            mem.supersede(sup["id"], added[sup["by"]] if sup["by"] is not None else "", sup["reason"])
        plan = {"added": added, "superseded": update.supersede, "references": update.references,
                "fallback": update.fallback, "problems": update.problems}
        self.log(f"--- ledger: +{len(added)} requirement(s), {len(update.supersede)} superseded"
                 + (" (fallback: request added verbatim)" if update.fallback else ""))
        return plan, tracer.summary()["total"].get("cost_usd", 0.0)

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"objects": []}

    def _write_summary(self) -> None:
        s = self.state
        summary = {"memory": self.cfg.memory, "forked_from": s.forked_from or None, "turns": s.turns,
                   "n_turns": len(s.turns), "approved_turns": sum(t["approved"] for t in s.turns),
                   "cost_usd": round(sum(t["cost_usd"] for t in s.turns), 6)}
        (self.dir / "session.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def run_session(requests: Iterable[str], cfg: SessionConfig, session_dir: str | Path, **kwargs) -> dict:
    """Feed the requests one at a time, each after the previous turn has finished. With resume=True, turns that
    already finished are skipped (their requests must match); with fork_from=(dir, k), the session continues
    from turn k of another session, and `requests` are the turns after it."""
    session = Session(session_dir, cfg, **kwargs)
    done = len(session.state.turns) if kwargs.get("resume") else 0
    for k, request in enumerate(requests, 1):
        if k <= done:
            if session.state.turns[k - 1]["request"] != request:
                raise ValueError(f"turn {k} of the saved session had a different request; start a new session")
            continue
        session.turn(request)
    return json.loads((session.dir / "session.json").read_text(encoding="utf-8"))
