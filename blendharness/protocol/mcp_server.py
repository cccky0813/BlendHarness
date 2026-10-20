"""BlendHarness as an MCP server: any MCP client (Claude Desktop, Claude Code, Cursor...) gets the sandboxed
Blender tools that our own Coder uses.

    blendharness-mcp [--skills-dir skills] [--run-dir runs/mcp_...]

The same ToolRegistry backs both our agent loop and this server, so external clients get exactly the
same behaviour: the static sandbox guard, all-or-nothing edits, error-as-observation, output truncation,
and a span in trace.jsonl for every call. Permission tiers become MCP tool annotations:

    read     -> readOnlyHint                       (view_script, inspect_scene)
    write    -> not read-only, not destructive     (load_skill)
    execute  -> destructiveHint: replaces the model (execute_bpy, edit_script)

Plus one tool only external clients need: render_views returns the renders as images, so the client's
own model can look at what it built (inside BlendHarness, the Critic does that).

Over stdio, stdout belongs to the protocol: nothing here may print. Blender logs go to a file.
"""

import argparse
import inspect
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import mcp_types
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from blendharness.core.llm import ToolCall
from blendharness.core.llm.providers import ROOT
from blendharness.core.tools import Tool, ToolRegistry
from blendharness.core.tracing import Tracer
from blendharness.memory.skills import SkillLibrary
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.blender_tools import BlenderTools
from blendharness.tools.skill_tools import SkillTools

INSTRUCTIONS = """Sandboxed headless Blender 4.4 for building 3D models with Python (bpy).

Workflow: write a COMPLETE bpy script and run it with execute_bpy (every run starts from an empty scene).
Check the result with inspect_scene, look at it with render_views, fix it with edit_script (all changes
in one call). Model around the origin, meters, +Z up. File system, process and network access are
blocked in scripts."""

ANNOTATIONS = {
    "read": mcp_types.ToolAnnotations(read_only_hint=True, open_world_hint=False),
    "write": mcp_types.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    "execute": mcp_types.ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
}


def _adapt(tool: Tool, registry: ToolRegistry, tracer: Tracer) -> Callable[..., str]:
    """An MCP tool function that runs `tool` through our registry (guard, truncation, tracing).

    The MCP SDK builds the argument schema from the function signature, so the wrapper carries the
    signature of the tool's own function (only the return type changes: the client gets text).
    """
    def run(**arguments) -> str:
        result = registry.execute(ToolCall(id=f"mcp_{uuid.uuid4().hex[:8]}", name=tool.name, arguments=arguments),
                                  tracer)
        if result.is_error:
            raise ToolError(result.content)        # -> isError: true; the client's model reads the message
        return result.content

    run.__signature__ = inspect.signature(tool.fn).replace(return_annotation=str)
    run.__name__ = tool.name
    return run


def build_server(blender: BlenderTools, tracer: Tracer, skill_tools: Optional[SkillTools] = None) -> MCPServer:
    registry = ToolRegistry(blender.as_tools() + (skill_tools.as_tools() if skill_tools else []))
    index = skill_tools.index_text() if skill_tools else ""
    instructions = INSTRUCTIONS + (f"\n\nVerified reusable functions (call load_skill(name) first):\n{index}"
                                   if index else "")
    server = MCPServer("blendharness", instructions=instructions)
    for tool in registry:
        server.add_tool(_adapt(tool, registry, tracer), name=tool.name, description=tool.description,
                        annotations=ANNOTATIONS[tool.permission])

    def render_views(num_views: int = 3, top_view: bool = True) -> list:
        """Render the current model from `num_views` angles around it (plus a top view)."""
        with tracer.span("tool", "render_views", num_views=num_views):
            if not blender.current_script:
                raise ToolError("there is no model yet; run a script with execute_bpy first")
            paths = blender.render(f"mcp_{time.strftime('%H%M%S')}", num_views=max(1, min(num_views, 6)),
                                   top_view=top_view)
            if not paths:
                raise ToolError("rendering failed; check that the script runs with execute_bpy")
            return [f"{len(paths)} renders of the current model (last one is the top view):"] + \
                   [Image(path=p) for p in paths]

    server.add_tool(render_views, name="render_views", annotations=ANNOTATIONS["read"], structured_output=False,
                    description="Render the current model as images (orbit views + top view) to check how it looks.")
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the BlendHarness Blender tools over MCP (stdio).")
    # Defaults are absolute (repository root, like .env and config/): an MCP client such as Claude Desktop
    # may start the server from any working directory, even "/".
    parser.add_argument("--skills-dir", default=str(ROOT / "skills"), help="skill library to offer ('' for none)")
    parser.add_argument("--run-dir", default=None, help="where trace, renders and blender.log go")
    args = parser.parse_args()

    run_dir = Path(args.run_dir or ROOT / "runs" / time.strftime("mcp_%Y%m%d_%H%M%S")).resolve()
    tracer = Tracer(run_dir, run_id=run_dir.name)
    skills_dir = Path(args.skills_dir) if args.skills_dir else None
    skill_tools = SkillTools(SkillLibrary(skills_dir)) if skills_dir and skills_dir.is_dir() else None
    with BlenderSession(run_dir / "blender.log") as session:
        blender = BlenderTools(session, run_dir / "renders",
                               preamble_provider=skill_tools.preamble if skill_tools else None)
        build_server(blender, tracer, skill_tools).run("stdio")


if __name__ == "__main__":
    main()
