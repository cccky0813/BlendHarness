"""Verifier: did the revision actually fix the Critic's issues, without breaking anything else?

Closed question per issue (FIXED / PARTIAL / NOT_FIXED) using two kinds of evidence:
  - visual: renders BEFORE and AFTER the revision
  - structural: Scene-Inspector diff (objects added/removed, moved, resized, materials, vertices)
Kept separate from the Critic so the judge of a fix is not the one who proposed it.
"""

import json
import re
from dataclasses import dataclass, field

from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient, image_message

VERIFIER_PROMPT = load_prompt("verifier")


@dataclass
class Verification:
    passed: bool
    issues: list[dict] = field(default_factory=list)       # per critic issue: index, verdict, evidence
    regressions: list[str] = field(default_factory=list)
    skipped_llm: bool = False                               # decided by the free structural pre-check
    parse_error: bool = False
    raw: str = ""

    def unresolved_text(self, critic_issues: list[dict]) -> str:
        """Feedback for the next Coder revision: only what is still broken."""
        lines = []
        for v in self.issues:
            if v.get("verdict") != "FIXED":
                i = v.get("index", 0) - 1
                orig = critic_issues[i] if 0 <= i < len(critic_issues) else {}
                lines.append(f"- STILL {v.get('verdict')}: {orig.get('problem', '')}\n"
                             f"  suggested fix: {orig.get('fix', '')}\n  verifier: {v.get('evidence', '')}")
        lines += [f"- REGRESSION: {r}" for r in self.regressions]
        return "\n".join(lines)


def scene_changed(diff: dict) -> bool:
    return bool(diff.get("added") or diff.get("removed") or diff.get("changed"))


def parse_verification(text: str, n_issues: int) -> Verification:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            issues = [i for i in data.get("issues", []) if isinstance(i, dict)]
            regressions = [str(r) for r in data.get("regressions", []) if r]
            all_fixed = len(issues) >= n_issues and all(i.get("verdict") == "FIXED" for i in issues)
            # Recompute "passed" from the parts instead of trusting the model's own summary flag.
            return Verification(all_fixed and not regressions, issues, regressions, raw=text)
        except json.JSONDecodeError:
            pass
    return Verification(False, [], [], parse_error=True, raw=text)  # fail safe: unparseable = not passed


def verify(llm: LLMClient, request: str, critic_issues: list[dict], before_renders: list[str],
           after_renders: list[str], diff: dict) -> Verification:
    with llm.tracer.span("agent", "verifier", n_issues=len(critic_issues)) as span:
        if "error" not in diff and not scene_changed(diff):
            # Free structural pre-check: nothing in the scene changed, so nothing can be fixed.
            result = Verification(
                False,
                [{"index": i + 1, "verdict": "NOT_FIXED", "evidence": "scene unchanged (Scene-Inspector diff empty)"}
                 for i in range(len(critic_issues))],
                skipped_llm=True,
            )
        else:
            problems = "\n".join(f"{i + 1}. {it.get('problem')} (suggested fix: {it.get('fix')})"
                                 for i, it in enumerate(critic_issues))
            text = (f"User request:\n{request}\n\nProblems found by the critic:\n{problems}\n\n"
                    f"Scene diff (before -> after):\n{json.dumps(diff, ensure_ascii=False)[:6000]}\n\n"
                    f"First {len(before_renders)} images: BEFORE. Last {len(after_renders)} images: AFTER.")
            user = image_message(text, before_renders + after_renders)
            resp = llm.chat([{"role": "system", "content": VERIFIER_PROMPT}, user], agent="verifier")
            result = parse_verification(resp.text, len(critic_issues))
        span.set(passed=result.passed, skipped_llm=result.skipped_llm, parse_error=result.parse_error,
                 verdicts=[i.get("verdict") for i in result.issues], n_regressions=len(result.regressions))
        return result
