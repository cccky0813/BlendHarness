"""Coder agent = role prompt + Blender tools + a model, run by the generic AgentLoop."""

from blendharness.agents.prompts import load_prompt
from blendharness.core.context_engine import (BudgetGuard, ContextEngine, OmitLargeToolArgs, OmitStaleCodeBlocks,
                                              Pin, tool_result_for)
from blendharness.core.llm import LLMClient
from blendharness.core.runtime import AgentLoop
from blendharness.tools.blender_tools import BlenderTools
from blendharness.tools.skill_tools import SkillTools

CODER_PROMPT = load_prompt("coder")


def executed_successfully(messages: list[dict]) -> str | None:
    """Completion check: the model may only finish after a script actually ran in THIS run.

    Catches the failure where the model writes the script into its reply instead of executing it.
    """
    ran_ok = any(_is_ok_run(m) for m in messages)
    if ran_ok:
        return None
    return ("you have not successfully executed any script in this task. Writing code in your reply "
            "does nothing; call execute_bpy with the complete script and make sure it runs.")


def _is_ok_run(m: dict) -> bool:
    """A tool result saying a script (full or edited) ran successfully."""
    content = str(m.get("content", ""))
    return m.get("role") == "tool" and content.startswith("OK:") and "script ran" in content


def _latest_failed_script(messages: list[dict]) -> str | None:
    """Id of the latest large script call, if that call failed (the model still has to fix it)."""
    latest = None
    for m in messages:
        for tc in m.get("tool_calls") or []:
            if len(tc["function"].get("arguments") or "") >= 1500:
                latest = tc["id"] if tool_result_for(messages, tc["id"]).startswith("ERROR") else None
    return latest


PIN_HEADER = ("CURRENT WORKING SCRIPT (always up to date; this is the text edit_script edits; "
              "loaded skills are prepended automatically when it runs):\n")


def make_coder_context(blender: BlenderTools, max_tokens: int = 100_000) -> ContextEngine:
    """Context pipeline (M3 v2):
    1. every script copy in the history becomes a placeholder, except the latest FAILED one (to be fixed);
    2. once a working script exists, code blocks in the task become placeholders too;
    3. the up-to-date working script is pinned at the END of the view, so the model always sees exactly
       one current version (no view_script round trips, no stale duplicates);
    4. a budget guard as the last line of defense (557 past calls: median 5.7k, max 23k tokens; should never fire).
    Lesson from eval m3_vs_baseline: hiding the script being edited made the model re-read it 17 times.
    """
    return ContextEngine([
        OmitLargeToolArgs(keep_last=0, keep=lambda msgs, tc: tc["id"] == _latest_failed_script(msgs)),
        OmitStaleCodeBlocks(lambda msgs, i: bool(blender.current_script)),
        Pin(lambda: f"{PIN_HEADER}```python\n{blender.current_script}\n```" if blender.current_script else None),
        BudgetGuard(max_tokens),
    ])


def make_coder(llm: LLMClient, blender: BlenderTools, skill_tools: SkillTools | None = None, max_steps: int = 15,
               max_cost_usd: float | None = 0.5, compact_context: bool = True,
               edit_tools: bool = True) -> AgentLoop:
    prompt = CODER_PROMPT
    index = skill_tools.index_text() if skill_tools else ""
    if index:  # progressive disclosure: only names + one-line descriptions sit in the context
        prompt += ("\nSkill library (verified reusable functions). If one fits a part of the request, call "
                   "load_skill(name) and then use the function in your script instead of writing it again:\n"
                   + index + "\n")
    return AgentLoop(
        context_policy=make_coder_context(blender) if compact_context else None,
        completion_check=executed_successfully,
        name="coder",
        llm=llm,
        system_prompt=prompt,
        tools=blender.as_tools(include_edit=edit_tools) + (skill_tools.as_tools() if skill_tools else []),
        max_steps=max_steps,
        max_same_error=3,
        max_cost_usd=max_cost_usd,
    )
