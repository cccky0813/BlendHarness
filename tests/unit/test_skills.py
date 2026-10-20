"""Skill library: static validation, storage round trip, progressive disclosure (no LLM, no Blender)."""

import pytest

from blendharness.memory.skills import Skill, SkillLibrary, validate_skill_code
from blendharness.tools.skill_tools import SkillTools

GOOD = '''"""A leg."""
import bpy


def build_leg(height=0.7, radius=0.03):
    """One cylindrical leg standing on z=0."""
    return None
'''


@pytest.mark.parametrize("name, code, problem", [
    ("leg", GOOD, None),
    ("Leg", GOOD, "invalid skill name"),
    ("leg", "def build_leg(:\n", "syntax error"),
    ("leg", "def make_leg():\n    pass\n", "must define a function build_leg"),
    # a top-level call would add objects to EVERY script the skill is prepended to
    ("leg", GOOD + "\nbuild_leg()\n", "top-level statement not allowed"),
    ("leg", GOOD + "\nSIZE = 2\n", "top-level statement not allowed"),
])
def test_validate_skill_code(name, code, problem):
    error = validate_skill_code(name, code)
    assert (error is None) if problem is None else (problem in error)


def test_add_get_round_trip_and_stats(tmp_path):
    lib = SkillLibrary(tmp_path)
    assert lib.add(Skill("leg", "A table leg.", GOOD, notes="Usage: build_leg()", tags=["furniture"])) is None
    assert lib.add(Skill("leg", "dup", GOOD)) == "skill 'leg' already exists"
    lib.record_use("leg", success=True)
    lib.record_use("leg", success=False)
    s = lib.get("leg")
    assert (s.description, s.tags, s.uses, s.successes) == ("A table leg.", ["furniture"], 2, 1)
    assert s.signature().startswith("def build_leg(height=0.7, radius=0.03)")
    assert lib.index_text() == "- leg: A table leg. [used 2x, 1 ok]"


def test_invalid_skill_is_not_stored(tmp_path):
    lib = SkillLibrary(tmp_path)
    assert "top-level" in lib.add(Skill("leg", "x", GOOD + "\nbuild_leg()\n"))
    assert lib.names() == []


def test_skill_tools_progressive_disclosure(tmp_path):
    lib = SkillLibrary(tmp_path)
    tools = SkillTools(lib)
    assert tools.as_tools() == [] and tools.preamble() == ""      # empty library: agent unchanged
    lib.add(Skill("leg", "A table leg.", GOOD))
    assert [t.name for t in tools.as_tools()] == ["load_skill"]
    assert tools.load_skill("missing").is_error
    r = tools.load_skill("leg")
    assert not r.is_error and "def build_leg(" in r.content
    tools.load_skill("leg")                                        # loading twice adds it once
    assert tools.preamble().count("# ---- skill: leg ----") == 1
