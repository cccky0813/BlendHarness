"""Librarian: turns an approved build into reusable skills (the "self-evolving" step of M4).

One call (no tools): read the request + the approved script + the existing skill index, and propose
0-3 parametric component functions. Proposals are only CANDIDATES: they become skills after the
admission gates in workflow/learning/admission.py (static checks, dedup, sandbox run).
"""

import json
import re
from dataclasses import dataclass

from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient
from blendharness.memory.skills import Skill

LIBRARIAN_PROMPT = load_prompt("librarian")


@dataclass
class Proposal:
    skills: list[Skill]
    parse_error: bool = False
    raw: str = ""


def parse_proposal(text: str, source_task: str) -> Proposal:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return Proposal([], parse_error=True, raw=text)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Proposal([], parse_error=True, raw=text)
    skills = []
    for item in data.get("skills", [])[:3]:
        if not isinstance(item, dict) or not item.get("name") or not item.get("code"):
            continue
        skills.append(Skill(
            name=str(item["name"]).strip(), description=str(item.get("description", "")).strip(),
            code=str(item["code"]), tags=[str(t) for t in item.get("tags", [])][:5],
            notes=f"Example: {item.get('usage', '').strip()}", source_task=source_task,
        ))
    return Proposal(skills, raw=text)


def propose_skills(llm: LLMClient, request: str, script: str, existing_index: str, source_task: str) -> Proposal:
    with llm.tracer.span("agent", "librarian") as span:
        user = (f"Request:\n{request}\n\nApproved script:\n```python\n{script}\n```\n\n"
                f"Existing skills:\n{existing_index or '(none yet)'}")
        resp = llm.chat([{"role": "system", "content": LIBRARIAN_PROMPT}, {"role": "user", "content": user}],
                        agent="librarian")
        proposal = parse_proposal(resp.text, source_task)
        span.set(n_proposed=len(proposal.skills), names=[s.name for s in proposal.skills],
                 parse_error=proposal.parse_error)
        return proposal


REVISE_PROMPT = load_prompt("librarian_revise")


def revise_skills(llm: LLMClient, rejected: list[tuple[Skill, str, str]], source_task: str) -> Proposal:
    """Repair round: rejection reasons go back to the model (error as observation), one attempt."""
    with llm.tracer.span("agent", "librarian_repair", n=len(rejected)) as span:
        parts = [f"### {sk.name} (rejected at {gate}: {reason})\ndescription: {sk.description}\n"
                 f"```python\n{sk.code}\n```" for sk, gate, reason in rejected]
        resp = llm.chat([{"role": "system", "content": REVISE_PROMPT},
                         {"role": "user", "content": "\n\n".join(parts)}], agent="librarian")
        proposal = parse_proposal(resp.text, source_task)
        span.set(n_revised=len(proposal.skills), parse_error=proposal.parse_error)
        return proposal
