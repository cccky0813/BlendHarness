"""SQLite store of a session's memory (docs/multiturn_design.md 5.3 / 5.9).

    requirements  the ledger: every requirement ever stated, with its status (active / superseded) and what replaced
                  it. Append-only: a requirement is never edited or deleted, only marked superseded with a reason,
                  so nothing the user asked for can silently disappear.
    objects       the scene registry, regenerated from the Scene-Inspector snapshot after every turn (the scene is
                  the source of truth, never a model's summary of it).
    episodes      one row per finished turn: what was asked, what changed, how the review went.

One file per session (`memory.db`), copied after every turn to `checkpoints/memory_tNN.db`: a resumed turn restarts
from the previous turn's copy (so redoing it cannot add a requirement twice), and a fork starts from the copy of the
turn it branches from.
"""

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS requirements (id TEXT PRIMARY KEY, turn INTEGER, text TEXT, status TEXT,
                                         superseded_by TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS objects (name TEXT PRIMARY KEY, grp TEXT, bbox TEXT, colors TEXT, added_turn INTEGER,
                                    changed_turn INTEGER, requirement TEXT);
CREATE TABLE IF NOT EXISTS episodes (turn INTEGER PRIMARY KEY, request TEXT, summary TEXT, data TEXT);
"""


class SessionMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ---------- snapshots (resume / fork) ----------
    def save_copy(self, target: str | Path) -> None:
        self.db.commit()
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(target)
        self.db.backup(dst)
        dst.close()

    @classmethod
    def restore(cls, copy: Optional[str | Path], path: str | Path) -> "SessionMemory":
        """Open `path` with the contents of `copy` (None = empty memory)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).unlink(missing_ok=True)
        if copy is not None and Path(copy).exists():
            shutil.copyfile(copy, path)
        return cls(path)

    # ---------- ledger ----------
    def requirements(self, active_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM requirements" + (" WHERE status = 'active'" if active_only else "")
        rows = [dict(r) for r in self.db.execute(q)]
        return sorted(rows, key=lambda r: int(r["id"][1:]))

    def add_requirement(self, turn: int, text: str) -> str:
        n = self.db.execute("SELECT COUNT(*) FROM requirements").fetchone()[0] + 1
        rid = f"r{n}"
        self.db.execute("INSERT INTO requirements VALUES (?, ?, ?, 'active', NULL, NULL)", (rid, turn, text.strip()))
        self.db.commit()
        return rid

    def supersede(self, rid: str, by: str, reason: str) -> None:
        self.db.execute("UPDATE requirements SET status = 'superseded', superseded_by = ?, reason = ? WHERE id = ?",
                        (by, reason, rid))
        self.db.commit()

    # ---------- registry ----------
    def objects(self) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM objects ORDER BY grp, name")]
        for r in rows:
            r["bbox"], r["colors"] = json.loads(r["bbox"] or "null"), json.loads(r["colors"] or "[]")
        return rows

    def set_objects(self, rows: list[dict[str, Any]]) -> None:
        self.db.execute("DELETE FROM objects")
        self.db.executemany("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(r["name"], r["grp"], json.dumps(r["bbox"]), json.dumps(r.get("colors") or []),
                              r["added_turn"], r["changed_turn"], r.get("requirement")) for r in rows])
        self.db.commit()

    # ---------- episodes ----------
    def episodes(self) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM episodes ORDER BY turn")]
        for r in rows:
            r["data"] = json.loads(r["data"] or "{}")
        return rows

    def add_episode(self, turn: int, request: str, summary: str, data: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO episodes VALUES (?, ?, ?, ?)",
                        (turn, request, summary, json.dumps(data, ensure_ascii=False)))
        self.db.commit()
