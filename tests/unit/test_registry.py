"""Tool registry: registration, permission tiers, selection, safe execution."""

import pytest

from blendharness.core.tools import Tool, ToolRegistry, ToolResult
from blendharness.tools.blender_tools import BlenderTools
from blendharness.tools.skill_tools import SkillTools
from blendharness.memory.skills import Skill, SkillLibrary
from tests.fakes import call

NO_ARGS = {"type": "object", "properties": {}}


def tool(name, permission="read", fn=lambda: "ok"):
    return Tool(name, f"{name} tool", NO_ARGS, fn, permission=permission)


def test_duplicate_names_are_rejected():
    with pytest.raises(ValueError, match="duplicate tool name 'look'"):
        ToolRegistry([tool("look"), tool("look")])


def test_unknown_permission_is_rejected():
    with pytest.raises(ValueError, match="permission must be one of"):
        tool("x", permission="admin")


def test_select_by_permission_and_exclude():
    reg = ToolRegistry([tool("look"), tool("load", "write"), tool("run", "execute")])
    assert reg.select(max_permission="read").names() == ["look"]
    assert reg.select(max_permission="write").names() == ["look", "load"]
    assert reg.select(exclude=["run"]).names() == ["look", "load"]
    assert len(reg) == 3                                  # select returns a new registry


def test_execute_turns_failures_into_observations(tracer):
    def boom(): raise RuntimeError("disk full")
    reg = ToolRegistry([tool("ok"), tool("boom", fn=boom)])
    assert reg.execute(call("ok", "1"), tracer).content == "ok"
    assert reg.execute(call("boom", "2"), tracer).content == "ERROR: RuntimeError: disk full"
    assert reg.execute(call("nope", "3"), tracer).content.startswith("ERROR: unknown tool 'nope'")
    assert tracer.spans[0].attrs["permission"] == "read"


def test_execute_passes_tool_results_through(tracer):
    reg = ToolRegistry([tool("err", fn=lambda: ToolResult("ERROR: bad", is_error=True))])
    assert reg.execute(call("err", "1"), tracer).is_error


def test_domain_tools_declare_their_permissions(fake_session, tmp_path):
    lib = SkillLibrary(tmp_path / "lib")
    lib.add(Skill("leg", "A leg.", 'def build_leg():\n    """A leg."""\n    return None\n'))
    reg = ToolRegistry(BlenderTools(fake_session, tmp_path).as_tools() + SkillTools(lib).as_tools())
    perms = {name: reg.get(name).permission for name in reg.names()}
    assert perms == {"execute_bpy": "execute", "edit_script": "execute", "view_script": "read",
                     "inspect_scene": "read", "load_skill": "write"}
    # e.g. a reviewer agent that may look at the scene but not change it:
    assert reg.select(max_permission="read").names() == ["view_script", "inspect_scene"]
