"""Critic: looks at the renders (+ structured scene summary) and lists major problems.

Not an agent loop: no tools, one multimodal call, structured JSON verdict.
"""

import json
import re
from dataclasses import dataclass, field

from harness.llm import LLMClient, image_message

CRITIC_PROMPT = """You are a strict visual critic for 3D models generated in Blender.

You get: the user's request, renders of the current model (orbit views at eye level plus a high
top-down view as the LAST image), and a structured list of the scene's objects.

Judge only MAJOR problems that make the result fail the request, for example:
missing or wrong parts, wrong proportions, parts floating or not attached, interpenetration,
wrong or missing materials/colors, placeholder primitive shapes where a real object was requested.
Ignore minor style choices and render quality (lighting/backdrop are not part of the model).

Reply with ONLY a JSON object, no other text:
{"approved": true or false,
 "issues": [{"problem": "<what is wrong, where>", "fix": "<concrete modeling change>"}]}
Rules: approved=true means no major problem (issues must be []). At most 3 issues, most important first.
"""


@dataclass
class Critique:
    approved: bool
    issues: list[dict] = field(default_factory=list)
    parse_error: bool = False
    raw: str = ""

    def as_text(self) -> str:
        if self.approved:
            return "APPROVED"
        return "\n".join(f"{i + 1}. PROBLEM: {it.get('problem')}\n   FIX: {it.get('fix')}"
                         for i, it in enumerate(self.issues))


def parse_critique(text: str) -> Critique:
    """Tolerant JSON extraction: models sometimes wrap JSON in ``` fences or add prose."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            issues = [i for i in data.get("issues", []) if isinstance(i, dict)][:3]
            approved = bool(data.get("approved")) and not issues
            return Critique(approved, issues, raw=text)
        except json.JSONDecodeError:
            pass
    # Unparseable: treat as "not approved" so a broken critic can never silently pass a bad model.
    return Critique(False, [{"problem": text.strip()[:500], "fix": ""}], parse_error=True, raw=text)


def critique(llm: LLMClient, request: str, renders: list[str], scene_text: str,
             system_prompt: str = CRITIC_PROMPT) -> Critique:
    with llm.tracer.span("agent", "critic", n_images=len(renders)) as span:
        user = image_message(
            f"User request:\n{request}\n\nScene objects:\n{scene_text}\n\n"
            f"{len(renders)} renders follow (last one is the top-down view).",
            renders,
        )
        resp = llm.chat([{"role": "system", "content": system_prompt}, user], agent="critic")
        result = parse_critique(resp.text)
        span.set(approved=result.approved, n_issues=len(result.issues), parse_error=result.parse_error,
                 cost_usd=resp.cost_usd)
        return result
