"""Lesson library: short rules learned from failed builds (semantic memory), next to the skill library
(procedural memory: reusable code).

A skill says HOW to build a part; a lesson says what to pay attention to, e.g. "derive every part's position
from the bounding box of the part it rests on, never from absolute numbers". Lessons go into the Coder's
system prompt.

Storage: one JSON file (lessons are few and small); writes are atomic like checkpoints.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Lesson:
    id: str                                      # short slug, e.g. "derive_positions_from_support"
    text: str                                    # the rule, one or two sentences, imperative
    category: str                                # failure category it addresses (analysis.taxonomy)
    sources: list[str] = field(default_factory=list)   # task ids whose failures support it
    evidence: list[str] = field(default_factory=list)  # the issues it was derived from (shortened)
    uses: int = 0                                # builds that had it in the prompt
    successes: int = 0                           # ... of which were approved


class LessonLibrary:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / "lessons.json"

    def all(self) -> list[Lesson]:
        if not self.path.exists():
            return []
        return [Lesson(**d) for d in json.loads(self.path.read_text(encoding="utf-8"))]

    def get(self, lesson_id: str) -> Optional[Lesson]:
        return next((x for x in self.all() if x.id == lesson_id), None)

    def add(self, lesson: Lesson) -> Optional[str]:
        """Store a lesson; returns an error message or None."""
        lessons = self.all()
        if any(x.id == lesson.id for x in lessons):
            return f"lesson {lesson.id!r} already exists"
        self._write(lessons + [lesson])
        return None

    def record_use(self, ids: list[str], success: bool) -> None:
        lessons = self.all()
        for x in lessons:
            if x.id in ids:
                x.uses += 1
                x.successes += int(success)
        self._write(lessons)

    def prompt_text(self) -> str:
        """The block that goes into the Coder's system prompt ('' when empty)."""
        lessons = self.all()
        if not lessons:
            return ""
        return "\n".join(f"- {x.text}" for x in lessons)

    def _write(self, lessons: list[Lesson]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps([asdict(x) for x in lessons], ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
