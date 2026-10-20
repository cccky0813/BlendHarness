"""Admission gates for candidate skills: only verified code enters long-term memory.

    1. static check   - name, build_<name> defined, no top-level side effects, sandbox policy (no execution)
    2. dedup          - same name, or a description too similar to an existing skill
    3. sandbox run    - call the function with its defaults in an EMPTY scene: it must run and
                        produce at least one mesh (free: no LLM call)
    4. quality        - (a) structure, free: built at the origin it must rest on the ground (lowest point
                        z ~ 0) and its parts must form one connected piece (bounding boxes touch);
                        (b) visual, ~$0.005: a render reviewed by the Critic against the description
                        ("runs" is not "correct": 3 of the first 9 extracted skills ran fine but were broken)

A wrong skill would be prepended to future scripts and pollute every later task, so it is better to
reject a good candidate than to admit a bad one (the same fail-safe idea as the Critic/Verifier).
"""

import json
import re
from dataclasses import dataclass, field

from typing import Optional

from blendharness.agents.critic import critique
from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient
from blendharness.memory.skills import Skill, SkillLibrary, validate_skill_code
from blendharness.sandbox.guard import check_code
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.blender_tools import BlenderTools

DUPLICATE_THRESHOLD = 0.6  # word-overlap (Jaccard) of name + description
GROUND_TOLERANCE = 0.02    # fraction of the component's height allowed below z = 0
CONTACT_TOLERANCE = 0.02   # fraction of the component's size allowed as a gap between parts


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 2}


def similarity(a: Skill, b: Skill) -> float:
    """Cheap lexical similarity. Fine for a few dozen skills; at scale use embeddings + rerank."""
    wa, wb = _words(f"{a.name} {a.description}"), _words(f"{b.name} {b.description}")
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def sandbox_check(session: BlenderSession, skill: Skill) -> str | None:
    """Run build_<name>() with defaults in an empty scene. Returns an error message or None."""
    probe = (skill.code + f"\n\n_objs = {skill.function_name}()\n"
             "import bpy as _bpy, json as _json\n"
             "print('BA_SKILL_CHECK=' + _json.dumps({'meshes': sum(o.type == 'MESH' for o in _bpy.context.scene.objects)}))\n")
    r = session.execute(probe, reset=True, timeout_s=60)
    if not r.ok:
        return f"sandbox run failed: {r.error}"
    for line in r.output.splitlines():
        if line.startswith("BA_SKILL_CHECK="):
            meshes = json.loads(line.split("=", 1)[1])["meshes"]
            return None if meshes > 0 else "sandbox run produced no mesh"
    return "sandbox run produced no report"


# A reusable component is judged differently from a user's final result: it only has to be recognizable,
# correctly assembled and not broken; details can be tuned when it is used. With the strict task Critic,
# 3 of 6 structurally sound skills were rejected for minor details (bolt orientation, handle depth).
SKILL_REVIEW_PROMPT = load_prompt("skill_review")


def structure_check(snapshot: dict) -> str | None:
    """Free geometric checks on the component built with default arguments at the origin."""
    meshes = [o for o in snapshot.get("objects", []) if o["type"] == "MESH" and o.get("bbox")]
    if not meshes:
        return "no mesh with a bounding box"
    lo = [min(o["bbox"]["min"][i] for o in meshes) for i in range(3)]
    hi = [max(o["bbox"]["max"][i] for o in meshes) for i in range(3)]
    size = max(hi[i] - lo[i] for i in range(3)) or 1.0
    tol = max(GROUND_TOLERANCE * size, 0.01)  # relative, but at least 1 cm (small parts like handles)
    if lo[2] < -tol:
        sunk = sorted((o for o in meshes if o["bbox"]["min"][2] < -tol), key=lambda o: o["bbox"]["min"][2])
        return (f"not grounded: lowest point z={lo[2]:.3f} (should be ~0); below ground: "
                + ", ".join(f"{o['name']} (min z {o['bbox']['min'][2]:.3f})" for o in sunk[:4]))
    gap = CONTACT_TOLERANCE * size
    def touch(a, b):
        return all(a["bbox"]["min"][i] - gap <= b["bbox"]["max"][i] and b["bbox"]["min"][i] - gap <= a["bbox"]["max"][i]
                   for i in range(3))
    connected, frontier = {meshes[0]["name"]}, [meshes[0]]
    while frontier:  # flood fill over the "bounding boxes touch" graph
        cur = frontier.pop()
        for o in meshes:
            if o["name"] not in connected and touch(cur, o):
                connected.add(o["name"])
                frontier.append(o)
    loose = [o["name"] for o in meshes if o["name"] not in connected]
    return f"parts not connected to the rest: {loose[:5]}" if loose else None


def quality_check(session: BlenderSession, skill: Skill, critic_llm: Optional[LLMClient], renders_dir) -> str | None:
    bt = BlenderTools(session, renders_dir)
    r = bt.execute_bpy(skill.code + f"\n\n{skill.function_name}()\n")
    if r.is_error:
        return f"could not rebuild: {r.content[:200]}"
    error = structure_check(bt.snapshot())
    if error or critic_llm is None:
        return error
    verdict = critique(critic_llm, f"A reusable 3D component: {skill.description}",
                       bt.render(f"skill_{skill.name}", num_views=2), bt.inspect_scene().content,
                       system_prompt=SKILL_REVIEW_PROMPT)
    if verdict.approved:
        return None
    return "visual review: " + "; ".join(i.get("problem", "") for i in verdict.issues)[:400]


@dataclass
class AdmissionResult:
    admitted: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)   # {"name", "gate", "reason"}
    rejected_skills: list[tuple[Skill, str, str]] = field(default_factory=list)  # (skill, gate, reason) for repair

    def reject(self, skill: Skill, gate: str, reason: str) -> None:
        self.rejected.append({"name": skill.name, "gate": gate, "reason": reason})
        self.rejected_skills.append((skill, gate, reason))


def admit(candidates: list[Skill], library: SkillLibrary, session: BlenderSession,
          critic_llm: Optional[LLMClient] = None, renders_dir="/tmp/skill_checks") -> AdmissionResult:
    result = AdmissionResult()
    for skill in candidates:
        error = validate_skill_code(skill.name, skill.code) or check_code(skill.code)
        if error:
            result.reject(skill, "static", error)
            continue
        existing = library.all()
        if skill.name in {s.name for s in existing}:
            result.reject(skill, "dedup", "name already exists")
            continue
        twin = max(existing, key=lambda s: similarity(skill, s), default=None)
        if twin and similarity(skill, twin) >= DUPLICATE_THRESHOLD:
            result.reject(skill, "dedup", f"too similar to {twin.name} ({similarity(skill, twin):.2f})")
            continue
        error = sandbox_check(session, skill)
        if error:
            result.reject(skill, "sandbox", error[:300])
            continue
        error = quality_check(session, skill, critic_llm, renders_dir)
        if error:
            gate = "visual" if error.startswith("visual") else "structure"
            result.reject(skill, gate, error[:400])
            continue
        library.add(skill)
        result.admitted.append(skill.name)
    return result
