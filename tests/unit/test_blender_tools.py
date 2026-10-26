"""BlenderTools logic (state model, edits, guards) on a fake session: no Blender needed.

FakeSession conventions (tests/fakes.py): `make_mesh("Name")` creates a mesh, a `raise` line fails.
"""

import pytest

from blendharness.tools.blender_tools import BlenderTools
from blendharness.tools.scene import diff_snapshots

CHAIR = 'make_mesh("Seat")\nmake_mesh("Leg")\nwidth = 0.5\n'


@pytest.fixture
def blender(fake_session, tmp_path):
    return BlenderTools(fake_session, tmp_path / "renders")


# ---------- execute_bpy ----------
def test_successful_run_becomes_current_script(blender):
    r = blender.execute_bpy(CHAIR)
    assert not r.is_error and r.content.startswith("OK: script ran")
    assert "2 objects" in r.content and blender.current_script == CHAIR


def test_failed_run_keeps_the_previous_script(blender):
    blender.execute_bpy(CHAIR)
    r = blender.execute_bpy(CHAIR + "raise RuntimeError('boom')\n")
    assert r.is_error and "RuntimeError: boom" in r.content
    assert blender.current_script == CHAIR
    assert [h["ok"] for h in blender.history] == [True, False]


def test_empty_scene_probe_does_not_wipe_the_model(blender):
    blender.execute_bpy(CHAIR)
    r = blender.execute_bpy("print('just checking')\n")
    assert r.is_error and "EMPTY scene" in r.content
    assert blender.current_script == CHAIR


# ---------- edit_script: all-or-nothing ----------
def test_batched_edits_apply_together(blender):
    blender.execute_bpy(CHAIR)
    r = blender.edit_script([{"old_string": "width = 0.5", "new_string": "width = 0.6"},
                             {"old_string": 'make_mesh("Leg")', "new_string": 'make_mesh("Leg")\nmake_mesh("Back")'}])
    assert not r.is_error and r.content.startswith("OK: 2 edit(s) applied")
    assert "width = 0.6" in blender.current_script and 'make_mesh("Back")' in blender.current_script


def test_one_unmatched_edit_applies_nothing(blender):
    blender.execute_bpy(CHAIR)
    r = blender.edit_script([{"old_string": "width = 0.5", "new_string": "width = 0.6"},
                             {"old_string": "height = 1", "new_string": "height = 2"}])
    assert r.is_error and "edit 2 of 2: old_string not found" in r.content
    assert blender.current_script == CHAIR


def test_ambiguous_edit_is_rejected(blender):
    blender.execute_bpy(CHAIR)
    r = blender.edit_script([{"old_string": "make_mesh", "new_string": "build"}])
    assert r.is_error and "occurs 2 times" in r.content


def test_edit_that_breaks_the_script_is_rolled_back(blender):
    blender.execute_bpy(CHAIR)
    r = blender.edit_script([{"old_string": "width = 0.5", "new_string": "raise ValueError('x')"}])
    assert r.is_error and "none of the 1 edits were applied" in r.content
    assert blender.current_script == CHAIR


def test_edit_without_a_script(blender):
    assert blender.edit_script([{"old_string": "a", "new_string": "b"}]).is_error


# ---------- view / inspect / preamble ----------
def test_view_script_pages_long_scripts(blender):
    long_script = 'make_mesh("A")\n' + "\n".join(f"x{i} = {i}" for i in range(200))
    blender.execute_bpy(long_script)
    r = blender.view_script()
    assert r.content.startswith("lines 1-120 of 201") and "start_line=121" in r.content
    assert blender.view_script(start_line=121).content.startswith("lines 121-201 of 201")


def test_inspect_scene_lists_objects(blender):
    blender.execute_bpy(CHAIR)
    r = blender.inspect_scene()
    assert r.content.splitlines()[0] == "2 objects, 16 vertices" and "- Seat (MESH)" in r.content


def test_inspect_scene_shows_where_the_geometry_is_not_only_the_origin(fake_session, tmp_path):
    snap = {"summary": {"object_count": 2, "mesh_object_count": 1, "total_vertex_count": 8}, "objects": [
        {"name": "Hook", "type": "MESH", "parent": None, "location": [0.0, 0.0, 0.0], "vertex_count": 8,
         "material_names": [], "bbox": {"min": [-0.5, -0.1, 1.7], "max": [-0.4, -0.05, 1.75], "size": [0.1, 0.05, 0.05]}},
        {"name": "Camera", "type": "CAMERA", "parent": None, "location": [3.0, 0.0, 2.0], "vertex_count": 0,
         "material_names": [], "bbox": None}]}
    blender = BlenderTools(fake_session, tmp_path)
    blender.current_script = "x = 1"
    blender.snapshot = lambda: snap
    lines = blender.inspect_scene().content.splitlines()
    assert lines[2] == ("- Hook (MESH) center=[-0.45, -0.075, 1.725] size=[0.1, 0.05, 0.05] z=1.7..1.75 "
                        "origin=[0.0, 0.0, 0.0] verts=8 materials=[]")
    assert lines[3] == "- Camera (CAMERA) origin=[3.0, 0.0, 2.0] verts=0 materials=[]"


def test_preamble_is_prepended_and_traceback_lines_are_mapped(fake_session, tmp_path):
    blender = BlenderTools(fake_session, tmp_path, preamble_provider=lambda: "def helper():\n    pass\n")
    blender.execute_bpy(CHAIR)
    assert fake_session.executed[0].startswith("def helper()")
    assert blender.full_script() == "def helper():\n    pass\n" + CHAIR
    assert blender._map_lines('File "<agent_code>", line 5') == 'File "<your script>", line 3'
    assert blender._map_lines('File "<agent_code>", line 1') == 'File "<skill code>", line 1'


# ---------- diff_snapshots (used by the Verifier) ----------
def obj(name, loc=(0, 0, 0), verts=8, mats=()):
    return {"name": name, "location": list(loc), "scale": [1, 1, 1], "dimensions": [1, 1, 1],
            "vertex_count": verts, "material_names": list(mats)}


def test_diff_snapshots():
    before = {"objects": [obj("Seat"), obj("Leg"), obj("Old")]}
    after = {"objects": [obj("Seat", loc=(0, 0, 0.1)), obj("Leg", mats=["Wood"]), obj("New")]}
    d = diff_snapshots(before, after)
    assert d["added"] == ["New"] and d["removed"] == ["Old"]
    assert [c["name"] for c in d["changed"]] == ["Leg", "Seat"]
    assert "location" in d["changed"][1] and "materials" in d["changed"][0]
    assert diff_snapshots({"error": "x"}, after) == {"error": "x"}


def test_diff_sees_a_recolor_of_the_same_material():
    roof = lambda color: {**obj("Roof", mats=["Clay"]), "material_colors": [color]}   # noqa: E731
    d = diff_snapshots({"objects": [roof([0.8, 0.5, 0.5])]}, {"objects": [roof([0.55, 0.15, 0.08])]})
    assert d["changed"] == [{"name": "Roof", "material_colors": "[[0.8, 0.5, 0.5]] -> [[0.55, 0.15, 0.08]]"}]
