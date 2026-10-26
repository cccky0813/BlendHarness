"""Multi-turn sessions: a user keeps asking for changes to the same scene, turn after turn.

    session dir/
      checkpoints/       t01_done.json, t02_done.json ...   (session state after each turn: resume or fork from any)
      turns/t01/         one run_build per turn: trace, renders, checkpoints, result.json
      session.json       summary of every turn so far

Each turn is one run_build: turn 1 builds from an empty scene; from turn 2 on, the build STARTS from the previous
turn's working script, so round 0 edits it, and the Critic / Verifier / revision rounds work exactly as in a single
build. The Coder sees the new request plus what earlier turns asked for (how much of it, and in which form, is the
`memory` strategy), and must declare what it changed (declare_changes), so regressions can be told apart from
intended edits.

The system only ever receives turn k's request as a string, after turn k-1 has finished: it never sees later turns,
and never sees an eval session's expected changes (eval/run_sessions.py keeps those to itself).
"""

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Optional

from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient
from blendharness.core.session import Checkpointer
from blendharness.sandbox.session import BlenderSession
from blendharness.workflow.build import BuildConfig, run_build

TURN_EDIT = load_prompt("turn_edit")
MEMORY_STRATEGIES = ("history",)          # M0; "structured" (M2) and "structured+retrieval" (M3) come next


@dataclass
class SessionConfig:
    build: BuildConfig = field(default_factory=BuildConfig)
    memory: str = "history"               # what the Coder is told about earlier turns

    def to_dict(self) -> dict:
        return {"build": asdict(self.build), "memory": self.memory}

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        return cls(BuildConfig(**d["build"]), d["memory"])


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


def turn_texts(earlier: list[str], request: str, memory: str) -> tuple[str, str]:
    """(task for the Coder, text the Critic and Verifier judge against) for the next turn.
    Takes only what has happened so far and the new request: there is nothing else to leak."""
    if not earlier:
        return request, request                       # turn 1: an ordinary build
    if memory not in MEMORY_STRATEGIES:
        raise NotImplementedError(f"memory strategy {memory!r} is not implemented yet")
    history = "\n".join(f"{i}. {r}" for i, r in enumerate(earlier, 1))
    turn = len(earlier) + 1
    task = TURN_EDIT.format(context="Earlier requests, oldest first (they still apply unless a later one changed "
                                    f"them):\n{history}", turn=turn, request=request)
    review = (f"Current request (turn {turn}):\n{request}\n\nEarlier requests that still apply unless a later one "
              f"changed them:\n{history}\n\nJudge mainly whether the current request was carried out. Check the "
              "earlier requests only for things this turn broke; do not reject the model for choices made in "
              "earlier turns.")
    return task, review


class Session:
    """A live session: start it, then call turn(request) once per user message."""

    def __init__(self, session_dir: str | Path, cfg: SessionConfig, log: Callable[[str], None] = print,
                 llm_factory: Callable[..., LLMClient] = LLMClient,
                 session_factory: Callable[[Path], BlenderSession] = BlenderSession,
                 resume: bool = False, fork_from: Optional[tuple[str | Path, int]] = None) -> None:
        self.dir = Path(session_dir)
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
            log(f"--- forked from {self.state.forked_from}")
        else:
            self.state = SessionState(config=cfg.to_dict())
        self.cfg = SessionConfig.from_dict(self.state.config)

    def turn(self, request: str) -> dict:
        """Run the next turn to the end (edit, critique, revisions) and return its record."""
        s = self.state
        k = len(s.turns) + 1
        task, review = turn_texts([t["request"] for t in s.turns], request, self.cfg.memory)
        turn_dir = self.dir / "turns" / f"t{k:02d}"
        unfinished = Checkpointer(turn_dir).labels() and not (turn_dir / "result.json").exists()
        build_cfg = replace(self.cfg.build, declare_changes=k > 1)
        self.log(f"=== turn {k}: {request[:100]}")
        record = run_build(task, build_cfg, turn_dir, log=self.log, llm_factory=self.llm_factory,
                           session_factory=self.session_factory, resume=bool(unfinished),
                           initial_script=s.current_script, review=review)
        after = (Checkpointer(turn_dir).load() or {}).get("current_script") or s.current_script
        s.turns.append({"turn": k, "request": request, "run_dir": str(turn_dir), "approved": record["approved"],
                        "status": record["status"], "n_rounds": record["n_rounds"],
                        "cost_usd": record["llm_total"].get("cost_usd", 0.0),
                        "declared_changes": record.get("declared_changes", []),
                        "script_changed": after != s.current_script})
        s.current_script = after
        self.checkpoints.save(f"t{k:02d}_done", s.to_dict())
        self._write_summary()
        return s.turns[-1]

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
