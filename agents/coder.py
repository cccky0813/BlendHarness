"""Coder agent = role prompt + Blender tools + a model, run by the generic AgentLoop."""

from harness.context import omit_large_tool_args, omit_stale_code_blocks
from harness.llm import LLMClient
from harness.loop import AgentLoop
from tools.blender_tools import BlenderTools

CODER_PROMPT = """You are an expert Blender 4.4.3 Python (bpy) modeler running in a headless Blender.

How you work:
1. Write a COMPLETE bpy script for the request and run it with execute_bpy.
   Every execution starts from an EMPTY scene, so always send the full script.
2. If it fails, read the error and traceback, fix the script and run it again.
3. When it runs, call inspect_scene and check that every requested part exists with
   plausible proportions and positions (parts touching, nothing floating or intersecting oddly).
   Fix and re-run if needed.
4. When the scene is right, reply with a short summary of what you built. Do not call tools then.

When REVISING an existing script (you are given the current script):
- Make targeted fixes with edit_script (exact old_string -> new_string), one call per separate change.
  Do not resend the whole script with execute_bpy unless most of it has to change.
- If an edit fails, the script is unchanged; use view_script if you need to see the current version.

Rules:
- Model the object around the origin, real-world scale in meters, +Z up.
- Prefer bpy.data / bmesh over bpy.ops where practical; give every object and material a clear name.
- Blender 4.4.3 API: Principled BSDF inputs are e.g. "Base Color", "Roughness", "Metallic",
  "Specular IOR Level", "Transmission Weight", "Emission Color".
- Do not add cameras or lights; rendering is handled separately.
"""


def executed_successfully(messages: list[dict]) -> str | None:
    """Completion check: the model may only finish after a script actually ran in THIS run.

    Catches the failure where the model writes the script into its reply instead of executing it.
    """
    ran_ok = any(m.get("role") == "tool" and str(m.get("content", "")).startswith(("OK: script ran", "OK: edit applied"))
                 for m in messages)
    if ran_ok:
        return None
    return ("you have not successfully executed any script in this task. Writing code in your reply "
            "does nothing; call execute_bpy with the complete script and make sure it runs.")


def _ran_ok_after(messages: list[dict], index: int) -> bool:
    """True if a script ran successfully after message `index` (so a script quoted there is outdated)."""
    return any(m.get("role") == "tool" and str(m.get("content", "")).startswith(("OK: script ran", "OK: edit applied"))
               for m in messages[index + 1:])


def coder_context(messages: list[dict]) -> list[dict]:
    """Context policy: keep only the newest full script in view; older ones become placeholders."""
    view = omit_large_tool_args(messages, keep_last=1)
    return omit_stale_code_blocks(view, _ran_ok_after)


def make_coder(llm: LLMClient, blender: BlenderTools, max_steps: int = 15,
               max_cost_usd: float | None = 0.5, compact_context: bool = True,
               edit_tools: bool = True) -> AgentLoop:
    return AgentLoop(
        context_policy=coder_context if compact_context else None,
        completion_check=executed_successfully,
        name="coder",
        llm=llm,
        system_prompt=CODER_PROMPT,
        tools=blender.as_tools(include_edit=edit_tools),
        max_steps=max_steps,
        max_same_error=3,
        max_cost_usd=max_cost_usd,
    )
