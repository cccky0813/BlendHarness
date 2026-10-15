"""Learning from an approved build: propose skills -> admission gates -> one repair round -> gates again.

Shared by the pipeline (learn_skills=True) and scripts/relearn.py.
"""

from pathlib import Path
from typing import Callable

from agents.librarian import propose_skills, revise_skills
from harness.llm import LLMClient
from memory.skill_admission import admit
from memory.skill_library import SkillLibrary
from tools.blender_session import BlenderSession

REPAIRABLE_GATES = {"static", "sandbox", "structure", "visual"}  # not "dedup": a duplicate is not a bug


def learn_from_build(librarian_llm: LLMClient, critic_llm: LLMClient, request: str, script: str,
                     library: SkillLibrary, session: BlenderSession, source_task: str, renders_dir: str | Path,
                     log: Callable[[str], None] = print) -> dict:
    proposal = propose_skills(librarian_llm, request, script, library.index_text(), source_task)
    first = admit(proposal.skills, library, session, critic_llm=critic_llm, renders_dir=renders_dir)
    log(f"--- librarian: proposed {len(proposal.skills)}, added {first.admitted}, "
        f"rejected {[r['name'] + ' (' + r['gate'] + ')' for r in first.rejected]}")

    repairable = [item for item in first.rejected_skills if item[1] in REPAIRABLE_GATES]
    repaired, rejected = [], [r for r in first.rejected if r["gate"] not in REPAIRABLE_GATES]
    if repairable:
        revision = revise_skills(librarian_llm, repairable, source_task)
        second = admit(revision.skills, library, session, critic_llm=critic_llm, renders_dir=renders_dir)
        repaired = second.admitted
        rejected += second.rejected
        revised_names = {s.name for s in revision.skills}
        rejected += [{"name": sk.name, "gate": gate, "reason": f"not revised ({reason[:150]})"}
                     for sk, gate, reason in repairable if sk.name not in revised_names]
        log(f"--- librarian repair: revised {len(revision.skills)}, added {repaired}, "
            f"still rejected {[r['name'] + ' (' + r['gate'] + ')' for r in second.rejected]}")

    return {"proposed": len(proposal.skills), "parse_error": proposal.parse_error,
            "added": first.admitted + repaired, "added_after_repair": repaired, "rejected": rejected}
