"""Reflector: reads failure cases from past trajectories and proposes lessons (rules) to avoid them.

One call per failure category (no tools). Like the Librarian's skills, proposals are only CANDIDATES:
they become lessons after the gates in workflow/learning/lessons.py, and only an A/B evaluation on unseen
tasks shows whether they help.
"""

import json
import re
from dataclasses import dataclass, field

from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient

REFLECTOR_PROMPT = load_prompt("reflector")


@dataclass
class Case:
    """One past problem: what the critic saw, what it suggested, how the revisions went."""
    task: str
    problem: str
    fix_hint: str
    verdicts: list[str]
    evidence: list[str]

    def render(self, number: int) -> str:
        if not self.verdicts:
            outcome = "never revised (the build ended first)"
        elif self.verdicts[-1] == "FIXED":
            outcome = f"fixed after {len(self.verdicts)} revision(s)"
        else:
            outcome = f"NOT fixed after {len(self.verdicts)} revision(s): {' / '.join(self.verdicts)}"
        lines = [f"Case {number} (task: {self.task})", f"  problem: {self.problem[:500]}"]
        if self.fix_hint:
            lines.append(f"  suggested fix: {self.fix_hint[:300]}")
        lines.append(f"  outcome: {outcome}")
        if self.evidence and self.evidence[-1]:
            lines.append(f"  verifier: {self.evidence[-1][:300]}")
        return "\n".join(lines)


@dataclass
class LessonProposal:
    id: str
    text: str
    category: str
    cases: list[int] = field(default_factory=list)


def parse_lessons(text: str) -> tuple[list[LessonProposal], bool]:
    """(proposals, parse_error). Tolerant of fences and prose around the JSON."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return [], True
    out = []
    for item in data.get("lessons", [])[:5]:
        if isinstance(item, dict) and item.get("id") and item.get("text"):
            cases = [int(c) for c in item.get("cases", []) if str(c).isdigit()]
            out.append(LessonProposal(str(item["id"]).strip(), str(item["text"]).strip(),
                                      str(item.get("category", "")).strip(), cases))
    return out, False


def reflect(llm: LLMClient, category: str, cases: list[Case], existing: str) -> tuple[list[LessonProposal], bool]:
    with llm.tracer.span("agent", "reflector", category=category, n_cases=len(cases)) as span:
        body = "\n\n".join(c.render(i) for i, c in enumerate(cases, 1))
        user = (f"Category: {category}\n\nExisting lessons:\n{existing or '(none yet)'}\n\n"
                f"Problems ({len(cases)} cases):\n\n{body}")
        resp = llm.chat([{"role": "system", "content": REFLECTOR_PROMPT}, {"role": "user", "content": user}],
                        agent="reflector")
        proposals, parse_error = parse_lessons(resp.text)
        span.set(n_proposed=len(proposals), ids=[p.id for p in proposals], parse_error=parse_error)
        return proposals, parse_error
