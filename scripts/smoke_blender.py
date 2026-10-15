"""Smoke test for the Blender sandbox (tools/blender_session.py). No LLM involved.

Checks: startup time, normal exec, error reporting, scene reset vs. keep,
watchdog on an infinite loop, crash recovery, and one real render.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.blender_session import BlenderSession  # noqa: E402

RUN = Path("runs/smoke_blender")
COUNT = "import bpy\nprint('objects:', len(bpy.data.objects))"


def show(title, r):
    status = "OK " if r.ok else "ERR"
    print(f"[{status}] {title:<34} {r.duration_s:>6}s  restarted={r.restarted}  "
          f"{(r.output.strip() or r.error)[:70]!r}")


with BlenderSession(RUN / "blender.log", exec_timeout_s=10) as bs:
    print(f"startup: {bs.startup_s}s | {bs.ping()}")

    show("1. add cube", bs.execute("import bpy\nbpy.ops.mesh.primitive_cube_add()\n" + COUNT))
    show("2. reset=True -> empty again", bs.execute(COUNT))
    bs.execute("import bpy\nbpy.ops.mesh.primitive_cube_add()")
    show("3. reset=False -> cube kept", bs.execute(COUNT, reset=False))

    r = bs.execute("import bpy\nmat = bpy.data.materials.new('M')\nmat.use_nodes = True\n"
                   "mat.node_tree.nodes['Principled BSDF'].inputs['Specular'].default_value = 0.5")
    show("4. Blender 4.4 API error", r)
    print("   traceback tail:", r.traceback.strip().splitlines()[-1] if r.traceback else "")

    show("5. infinite loop (timeout 10s)", bs.execute("while True:\n    pass"))
    show("6. works after restart", bs.execute(COUNT))
    show("7. sys.exit() inside code", bs.execute("import sys\nsys.exit(1)"))
    show("8. server still alive", bs.execute(COUNT))

    out = (RUN / "render.png").resolve()
    t = time.perf_counter()
    r = bs.execute(f"""
import bpy
bpy.ops.mesh.primitive_monkey_add()
bpy.ops.object.camera_add(location=(3, -3, 2), rotation=(1.1, 0, 0.8))
scene = bpy.context.scene
scene.camera = bpy.context.object
bpy.ops.object.light_add(type='SUN', location=(2, -2, 4))
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.filepath = r"{out}"
bpy.ops.render.render(write_still=True)
print('rendered')
""")
    show("9. render 512px", r)
    print(f"   file exists: {out.exists()}  ({out})")
    print(f"restarts during test: {bs.restarts}")
