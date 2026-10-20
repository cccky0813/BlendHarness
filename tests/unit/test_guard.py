"""Static sandbox policy: what model-written code may and may not do, checked before it runs."""

import pytest

from blendharness.sandbox.guard import check_code
from blendharness.tools.blender_tools import BlenderTools
from blendharness.workflow.learning.admission import admit
from blendharness.memory.skills import Skill, SkillLibrary

# A real script from run 20261015_143127 (village task, M3 v1): the context policy had hidden the script
# the Coder was editing, so it searched the host disk and read our own payload logs to get it back.
SNOOPING_SCRIPT = '''import os
base = "/Users/someone/blender-agent/runs"
for root, dirs, files in os.walk(base):
    for f in files:
        s = open(os.path.join(root, f), errors="ignore").read()
'''

MODELING_SCRIPT = '''import bpy, bmesh, math, random
from mathutils import Vector, Matrix
bpy.ops.mesh.primitive_cube_add(size=1)
obj = bpy.context.object
obj.rotation_euler = (0, 0, math.radians(30))
obj.matrix_world = __import__('mathutils').Matrix.Rotation(1.57, 4, 'X')   # inline import seen in real runs
mat = bpy.data.materials.new("Wood")
'''


def test_normal_modeling_code_passes():
    assert check_code(MODELING_SCRIPT) is None


def test_the_real_snooping_script_is_blocked():
    problem = check_code(SNOOPING_SCRIPT)
    assert problem.startswith("blocked by the sandbox policy: `import os` is not allowed (line 1)")
    assert "file system, process and network access are disabled" in problem


@pytest.mark.parametrize("code, expected", [
    ("import subprocess\n", "`import subprocess`"),
    ("from os.path import join\n", "`from os.path import ...`"),
    ("import urllib.request\n", "`import urllib.request`"),
    ("x = 1\ndata = open('/etc/passwd').read()\n", "calling `open()` is not allowed (line 2)"),
    ("eval('1+1')\n", "calling `eval()`"),
    ("__import__('os').system('ls')\n", "calling `__import__()`"),
    ("m = 'os'\n__import__(m)\n", "calling `__import__()`"),                    # dynamic: can't be checked
    ("().__class__.__bases__[0].__subclasses__()\n", "`.__subclasses__`"),
    ("f = lambda: 0\nf.__globals__\n", "`.__globals__`"),
    ("__builtins__['open']\n", "`__builtins__`"),
    ("import bpy\nbpy.ops.wm.quit_blender()\n", "`bpy.ops.wm.quit_blender`"),
    ("import bpy\nbpy.ops.wm.save_as_mainfile(filepath='/tmp/x.blend')\n", "`bpy.ops.wm.save_as_mainfile`"),
])
def test_blocked_patterns(code, expected):
    problem = check_code(code)
    assert problem is not None and expected in problem


def test_syntax_errors_are_left_to_python():
    assert check_code("def broken(:\n") is None        # running it gives the model a better message


def test_other_wm_operators_and_names_are_fine():
    assert check_code("import bpy\nbpy.ops.wm.redraw_timer(type='DRAW', iterations=1)\nopen_door = True\n") is None


# ---------- wired into execution and skill admission ----------
def test_blocked_code_never_reaches_blender(fake_session, tmp_path):
    blender = BlenderTools(fake_session, tmp_path)
    r = blender.execute_bpy(SNOOPING_SCRIPT)
    assert r.is_error and "sandbox policy" in r.content
    assert fake_session.executed == []                   # nothing was sent to the sandbox
    assert blender.history[-1]["ok"] is False and blender.current_script == ""


def test_blocked_edit_keeps_the_current_script(fake_session, tmp_path):
    blender = BlenderTools(fake_session, tmp_path)
    blender.execute_bpy('make_mesh("Seat")\n')
    r = blender.edit_script([{"old_string": 'make_mesh("Seat")', "new_string": 'import os\nmake_mesh("Seat")'}])
    assert r.is_error and "sandbox policy" in r.content
    assert blender.current_script == 'make_mesh("Seat")\n'


def test_skill_admission_rejects_policy_violations_statically(fake_session, tmp_path):
    code = 'import os\n\n\ndef build_leg():\n    """A leg."""\n    return os.getcwd()\n'
    result = admit([Skill("leg", "A table leg.", code)], SkillLibrary(tmp_path / "lib"), fake_session)
    assert result.rejected[0]["gate"] == "static" and "sandbox policy" in result.rejected[0]["reason"]
    assert fake_session.executed == []
