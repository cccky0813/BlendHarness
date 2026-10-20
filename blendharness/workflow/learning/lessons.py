"""Learning lessons from failures: past trajectories -> cases -> Reflector -> gates -> lesson library.

    1. cases     every Critic problem of the target categories, with its suggested fix and what happened
    2. reflect   one Reflector call per category proposes 2-5 rules
    3. gates     static (id, length, category) -> generality (the cited cases come from >= 2 different
                 tasks, checked by us, not taken from the model's word) -> dedup (word overlap)
    4. store     memory/lessons with its sources and evidence

The gates only keep obviously bad rules out. Whether the lessons HELP is decided by an A/B evaluation on
unseen tasks (the real gate of self-evolution), not here.
"""

import re
from typing import Callable, Iterable

from blendharness.agents.reflector import Case, reflect
from blendharness.analysis.taxonomy import CATEGORIES
from blendharness.analysis.trajectory import Trajectory
from blendharness.core.llm import LLMClient
from blendharness.memory.lessons import Lesson, LessonLibrary

ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
MIN_CHARS, MAX_CHARS = 40, 350
MIN_TASKS = 2
DUPLICATE_THRESHOLD = 0.6


def task_label(t: Trajectory) -> str:
    """Builds of the same prompt are the same task, whatever experiment they ran in."""
    return " ".join(t.prompt.split())[:70]


def select_cases(trajectories: Iterable[Trajectory], category: str) -> list[Case]:
    return [Case(task_label(t), i.text, i.fix_hint, list(i.verdicts), list(i.evidence))
            for t in trajectories for i in t.issues if i.category == category]


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 3}


def similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def check_lesson(lesson_id: str, text: str, category: str, cited_tasks: set[str], existing: list[Lesson]) -> tuple[str, str] | None:
    """(gate, reason) of the first failed gate, or None if the lesson may be stored."""
    if not ID_RE.match(lesson_id):
        return "static", f"invalid id {lesson_id!r}"
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        return "static", f"length {len(text)} outside {MIN_CHARS}-{MAX_CHARS} chars"
    if category not in CATEGORIES:
        return "static", f"unknown category {category!r}"
    if len(cited_tasks) < MIN_TASKS:
        return "generality", f"supported by {len(cited_tasks)} task(s), needs >= {MIN_TASKS}"
    for other in existing:
        if other.id == lesson_id:
            return "dedup", f"id {lesson_id!r} exists"
        if similarity(text, other.text) >= DUPLICATE_THRESHOLD:
            return "dedup", f"too similar to {other.id}"
    return None


def learn_lessons(llm: LLMClient, trajectories: list[Trajectory], categories: list[str], library: LessonLibrary,
                  log: Callable[[str], None] = print) -> dict:
    report = {"categories": {}, "added": [], "rejected": []}
    for category in categories:
        cases = select_cases(trajectories, category)
        if len({c.task for c in cases}) < MIN_TASKS:
            log(f"--- {category}: {len(cases)} cases from too few tasks, skipped")
            continue
        proposals, parse_error = reflect(llm, category, cases, library.prompt_text())
        report["categories"][category] = {"cases": len(cases), "proposed": len(proposals), "parse_error": parse_error}
        added = []
        for p in proposals:
            cited = [cases[n - 1] for n in p.cases if 0 < n <= len(cases)]   # citations checked by us
            failed = check_lesson(p.id, p.text, category, {c.task for c in cited}, library.all())
            if failed:
                report["rejected"].append({"id": p.id, "gate": failed[0], "reason": failed[1], "text": p.text})
                continue
            library.add(Lesson(p.id, p.text, category, sources=sorted({c.task for c in cited}),
                               evidence=[c.problem[:200] for c in cited]))
            added.append(p.id)
        report["added"] += added
        log(f"--- {category}: {len(cases)} cases -> proposed {len(proposals)}, added {added}")
    return report
