"""Coder agent = role prompt + Blender tools + a model, run by the generic AgentLoop."""

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

Rules:
- Model the object around the origin, real-world scale in meters, +Z up.
- Prefer bpy.data / bmesh over bpy.ops where practical; give every object and material a clear name.
- Blender 4.4.3 API: Principled BSDF inputs are e.g. "Base Color", "Roughness", "Metallic",
  "Specular IOR Level", "Transmission Weight", "Emission Color".
- Do not add cameras or lights; rendering is handled separately.
"""


def make_coder(llm: LLMClient, blender: BlenderTools, max_steps: int = 15,
               max_cost_usd: float | None = 0.5) -> AgentLoop:
    return AgentLoop(
        name="coder",
        llm=llm,
        system_prompt=CODER_PROMPT,
        tools=blender.as_tools(),
        max_steps=max_steps,
        max_same_error=3,
        max_cost_usd=max_cost_usd,
    )
