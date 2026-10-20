"""BuildState: everything needed to continue a build, as plain JSON (saved at every checkpoint).

Live objects (LLM clients, the Blender session, agents) are NOT state: they are rebuilt from `config`.
The Blender scene is not saved either: it is rebuilt by re-running `current_script` (every execution
starts from an empty scene, so the script alone fully determines the scene).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class BuildState:
    prompt: str
    config: dict[str, Any]                       # asdict(BuildConfig)
    completed_rounds: int = -1                   # -1 nothing yet | 0 build + critique | k revision k
    rounds_finished: bool = False                # no more revision rounds (approved / no script / rounds used)
    approved: bool = False
    current_script: str = ""                     # last script that ran OK
    loaded_skills: list[str] = field(default_factory=list)
    lesson_ids: list[str] = field(default_factory=list)      # lessons in the Coder's prompt, fixed at the start
    exec_history: list[dict] = field(default_factory=list)   # every attempted script and its outcome
    open_issues: list[dict] = field(default_factory=list)    # what the next revision must fix
    feedback: str = ""                           # the text the next revision is told
    last_renders: list[str] = field(default_factory=list)    # "before" images for the next verification
    rounds_log: list[dict] = field(default_factory=list)
    learned: Optional[dict] = None               # skill learning result, once done
    elapsed_s: float = 0.0                       # wall time over all processes that worked on this build
    sandbox_restarts: int = 0
    forked_from: str = ""                        # "<run dir>@<checkpoint label>" if this build is a fork

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildState":
        return cls(**data)
