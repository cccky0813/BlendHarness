"""The MCP server, through a real MCP client connected in-process (fake Blender, no network)."""

import asyncio
import json

import pytest

pytest.importorskip("mcp", reason='MCP is optional: pip install -e ".[mcp]"')
from mcp import Client  # noqa: E402

from blendharness.memory.skills import Skill, SkillLibrary  # noqa: E402
from blendharness.protocol.mcp_server import build_server  # noqa: E402
from blendharness.tools.blender_tools import BlenderTools  # noqa: E402
from blendharness.tools.skill_tools import SkillTools  # noqa: E402

LEG = 'def build_leg():\n    """A leg."""\n    return None\n'


@pytest.fixture
def server(fake_session, tracer, tmp_path):
    lib = SkillLibrary(tmp_path / "lib")
    lib.add(Skill("leg", "A table leg.", LEG))
    skills = SkillTools(lib)
    blender = BlenderTools(fake_session, tmp_path / "renders", preamble_provider=skills.preamble)
    return build_server(blender, tracer, skills)


def session(server, steps):
    """Run `steps(client)` against the server over a real MCP connection."""
    async def main():
        async with Client(server) as client:
            return await steps(client)
    return asyncio.run(main())


def text(result) -> str:
    return "".join(c.text for c in result.content if c.type == "text")


def test_tools_schemas_and_annotations(server):
    async def steps(c):
        return (await c.list_tools()).tools
    tools = {t.name: t for t in session(server, steps)}
    assert set(tools) == {"execute_bpy", "edit_script", "view_script", "inspect_scene", "load_skill", "render_views"}
    assert tools["inspect_scene"].annotations.read_only_hint is True
    assert tools["execute_bpy"].annotations.destructive_hint is True
    assert tools["load_skill"].annotations.destructive_hint is False
    assert tools["execute_bpy"].input_schema["required"] == ["code"]
    edit_item = json.dumps(tools["edit_script"].input_schema)
    assert "old_string" in edit_item and "new_string" in edit_item        # from the Edit TypedDict


def test_instructions_list_the_skills(server):
    assert "leg: A table leg." in server.instructions


def test_build_inspect_edit_over_mcp(server, tracer):
    async def steps(c):
        built = await c.call_tool("execute_bpy", {"code": 'make_mesh("Seat")\n'})
        edited = await c.call_tool("edit_script", {"edits": [{"old_string": 'make_mesh("Seat")',
                                                             "new_string": 'make_mesh("Seat")\nmake_mesh("Back")'}]})
        scene = await c.call_tool("inspect_scene", {})
        return built, edited, scene
    built, edited, scene = session(server, steps)
    assert not built.is_error and text(built).startswith("OK: script ran")
    assert text(edited).startswith("OK: 1 edit(s) applied")
    assert "- Back (MESH)" in text(scene)
    assert [s.name for s in tracer.spans if s.kind == "tool"] == ["execute_bpy", "edit_script", "inspect_scene"]


def test_sandbox_guard_applies_to_external_clients(server, fake_session):
    async def steps(c):
        return await c.call_tool("execute_bpy", {"code": "import os\nos.listdir('/')\n"})
    result = session(server, steps)
    assert result.is_error and "blocked by the sandbox policy" in text(result)
    assert fake_session.executed == []


def test_render_without_a_model_is_an_error(server):
    async def steps(c):
        return await c.call_tool("render_views", {})
    result = session(server, steps)
    assert result.is_error and "no model yet" in text(result)


def test_skills_work_over_mcp(server, fake_session):
    async def steps(c):
        loaded = await c.call_tool("load_skill", {"name": "leg"})
        await c.call_tool("execute_bpy", {"code": 'make_mesh("Top")\n'})
        return loaded
    loaded = session(server, steps)
    assert "def build_leg(" in text(loaded)
    assert fake_session.executed[0].startswith("# ---- skill: leg ----")     # prepended to the script
