"""The real sandbox: one headless Blender for the whole module (startup takes a few seconds).

Skipped automatically when Blender is not installed. Run only these: pytest -m blender
"""

import os
from pathlib import Path

import pytest

from blendharness.sandbox.session import DEFAULT_BLENDER, BlenderSession
from blendharness.tools.blender_tools import BlenderTools

pytestmark = [
    pytest.mark.blender,
    pytest.mark.skipif(not Path(os.environ.get("BLENDER_PATH", DEFAULT_BLENDER)).exists(),
                       reason="Blender not installed"),
]

COUNT = "import bpy\nprint('objects:', len(bpy.data.objects))"
CUBE = "import bpy\nbpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))\nbpy.context.object.name = 'Box'\n"


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    with BlenderSession(tmp_path_factory.mktemp("blender") / "blender.log", exec_timeout_s=10) as s:
        yield s


def test_every_run_starts_from_an_empty_scene(session):
    assert "objects: 1" in session.execute(CUBE + COUNT).output
    assert "objects: 0" in session.execute(COUNT).output
    session.execute(CUBE)
    assert "objects: 1" in session.execute(COUNT, reset=False).output


def test_errors_point_at_the_agent_code(session):
    r = session.execute("import bpy\nx = 1\nbpy.data.objects['missing']")
    assert not r.ok and "KeyError" in r.error
    assert 'File "<agent_code>", line 3' in r.traceback


def test_watchdog_restarts_after_an_infinite_loop(session):
    r = session.execute("while True:\n    pass", timeout_s=3)
    assert not r.ok and r.restarted
    assert "objects: 0" in session.execute(COUNT).output        # usable again


def test_blender_tools_on_the_real_sandbox(session, tmp_path):
    blender = BlenderTools(session, tmp_path)
    assert not blender.execute_bpy(CUBE).is_error
    snap = blender.snapshot()
    assert [o["name"] for o in snap["objects"]] == ["Box"]
    assert blender.edit_script([{"old_string": "location=(0, 0, 0.5)", "new_string": "location=(0, 0, 1.5)"}]).content.startswith("OK")
    assert blender.snapshot()["objects"][0]["location"] == [0.0, 0.0, 1.5]
