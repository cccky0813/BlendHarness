"""Checkpoints: save the state of a long task at safe points, so it can resume or fork from any of them.

Rules (from the design in docs/roadmap.md):
  - state is plain JSON: no clients, sockets or other live objects, so any process can load it;
  - save only at safe points (a phase fully finished), never mid-phase;
  - atomic write: write `<label>.json.tmp`, then rename. A crash during the write leaves the previous
    checkpoint intact instead of a half-written file (rename is atomic on POSIX file systems).

Layout: <run_dir>/checkpoints/<seq>_<label>.json, e.g. 000_round0.json, 001_round1.json.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional


class Checkpointer:
    def __init__(self, run_dir: str | Path) -> None:
        self.dir = Path(run_dir) / "checkpoints"

    def save(self, label: str, state: dict[str, Any]) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        seq = len(self.paths())
        path = self.dir / f"{seq:03d}_{label}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"label": label, "seq": seq, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                   "state": state}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def paths(self) -> list[Path]:
        """All checkpoints, oldest first."""
        return sorted(self.dir.glob("[0-9][0-9][0-9]_*.json")) if self.dir.exists() else []

    def labels(self) -> list[str]:
        return [p.stem.split("_", 1)[1] for p in self.paths()]

    def load(self, label: Optional[str] = None) -> Optional[dict[str, Any]]:
        """The state saved under `label` (its latest save), or the latest checkpoint if no label is given."""
        paths = [p for p in self.paths() if label is None or p.stem.split("_", 1)[1] == label]
        return json.loads(paths[-1].read_text(encoding="utf-8"))["state"] if paths else None
