"""Librarian: turns an approved build into reusable skills (the "self-evolving" step of M4).

One call (no tools): read the request + the approved script + the existing skill index, and propose
0-3 parametric component functions. Proposals are only CANDIDATES: they become skills after the
admission gates in memory/skill_admission.py (static checks, dedup, sandbox run).
"""

import json
import re
from dataclasses import dataclass

from harness.llm import LLMClient
from memory.skill_library import Skill

LIBRARIAN_PROMPT = """You maintain a library of reusable Blender 4.4 Python (bpy) modeling functions ("skills").

You get a modeling request, a script that was APPROVED by a visual review, and the list of existing skills.
Extract 0 to 3 components that are likely to be reused in OTHER tasks (e.g. a chair leg set, a mug handle,
a gable-roof house, a low-poly tree, a cabinet with doors) — not the whole scene, not trivial primitives,
and not something an existing skill already covers.

Each skill is ONE self-contained function named build_<name>:
- module level may contain only imports and the function (no calls, no variables)
- parameters: at least `location` (x, y, z) and `name` (prefix for created object names), plus the few
  dimensions that matter (sizes in meters); every parameter has a sensible default
- builds the component in a fresh scene with default arguments and returns the list of created objects
- the component RESTS ON the location: its lowest point is at location z (nothing below it), and all its
  parts touch each other as one assembled piece (re-derive every part's offset from the approved script)
- creates its own materials if it needs them (get-or-create by name), uses the Blender 4.4 API
- keep the geometry/quality of the approved script; generalize hard-coded positions into parameters

Reply with ONLY a JSON object:
{"skills": [{"name": "snake_case_name", "description": "<one line: what it builds + key parameters>",
             "tags": ["furniture", ...], "usage": "<one-line example call>", "code": "<python source>"}]}
Return {"skills": []} if nothing is worth keeping.
"""


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
