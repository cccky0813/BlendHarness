"""Coder agent = role prompt + Blender tools + a model, run by the generic AgentLoop."""

from blendharness.agents.prompts import load_prompt
from blendharness.core.context_engine import omit_large_tool_args, omit_stale_code_blocks, pin_at_end, tool_result_for
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


def _failed(messages: list[dict], call: dict) -> bool:
    return tool_result_for(messages, call["id"]).startswith("ERROR")


def make_coder_context(blender: BlenderTools):
    """Context policy (M3 v2):
    - every script copy in the history becomes a placeholder, except the latest FAILED one (to be fixed);
    - code blocks in the task become placeholders once a working script exists;
    - the up-to-date working script is pinned at the END of the view, so the model always sees exactly
      one current version (no view_script round trips, no stale duplicates).
    Lesson from eval m3_vs_baseline: hiding the script being edited made the model re-read it 17 times.
    """
    def policy(messages: list[dict]) -> list[dict]:
        latest_failed = None
        for m in messages:
            for tc in m.get("tool_calls") or []:
                if len(tc["function"].get("arguments") or "") >= 1500:
                    latest_failed = tc["id"] if _failed(messages, tc) else None
        view = omit_large_tool_args(messages, keep_last=0, keep=lambda msgs, tc: tc["id"] == latest_failed)
        if not blender.current_script:
            return view
        view = omit_stale_code_blocks(view, lambda msgs, i: True)
        return pin_at_end(view, "CURRENT WORKING SCRIPT (always up to date; this is the text edit_script edits; "
                                "loaded skills are prepended automatically when it runs):\n"
                                f"```python\n{blender.current_script}\n```")
    return policy


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
