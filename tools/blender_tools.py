"""Blender tools for agents, built on the sandbox (BlenderSession).

State model: the ONLY state is `current_script`, the last script that ran without error.
Every exec starts from an empty scene, so render/inspect simply re-run current_script first.
No hidden Blender state -> reproducible, and survives a sandbox restart.

The render script and Scene-Inspector are ported from the original LL3M_demo1 project.
"""

import json
import time
from pathlib import Path
from typing import Optional

from harness.loop import Tool, ToolResult
from tools.blender_session import BlenderSession

_RENDER_PREFIX = "BA_RENDER_RESULT="
_SCENE_PREFIX = "BA_SCENE_RESULT="


class BlenderTools:
    def __init__(self, session: BlenderSession, renders_dir: str | Path, resolution: int = 512) -> None:
        self.session = session
        self.renders_dir = Path(renders_dir).resolve()
        self.resolution = resolution
        self.current_script = ""          # last script that executed successfully
        self.history: list[dict] = []     # every attempted script and its outcome

    # ---------- tool functions (what the agent calls) ----------
    def execute_bpy(self, code: str) -> ToolResult:
        r = self.session.execute(code, reset=True)
        self.history.append({"code": code, "ok": r.ok, "error": r.error})
        if not r.ok:
            msg = f"ERROR: {r.error}\n{r.traceback}".strip()
            if r.output.strip():
                msg += f"\n--- stdout before the error ---\n{r.output.strip()[-1500:]}"
            return ToolResult(msg, is_error=True)
        self.current_script = code
        summary = self._scene_summary_line()
        out = r.output.strip()
        return ToolResult(f"OK: script ran in {r.duration_s}s. {summary}" + (f"\n--- stdout ---\n{out[-1500:]}" if out else ""))

    def inspect_scene(self) -> ToolResult:
        snap = self.snapshot()
        if "error" in snap:
            return ToolResult(f"ERROR: {snap['error']}", is_error=True)
        lines = [f"{snap['summary']['object_count']} objects, {snap['summary']['total_vertex_count']} vertices"]
        for o in snap["objects"]:
            bbox = o.get("bbox") or {}
            lines.append(
                f"- {o['name']} ({o['type']}) loc={o['location']} size={bbox.get('size')} "
                f"verts={o['vertex_count']} materials={o['material_names']}"
                + (f" parent={o['parent']}" if o["parent"] else "")
            )
        return ToolResult("\n".join(lines))

    # ---------- helpers used by the harness / other agents ----------
    def snapshot(self) -> dict:
        """Structured scene state (Scene-Inspector) of current_script."""
        if not self.current_script:
            return {"error": "no successful script yet; call execute_bpy first"}
        r = self.session.execute(self.current_script + "\n" + _SCENE_INSPECTOR_SCRIPT, reset=True)
        if not r.ok:
            return {"error": r.error}
        return _extract_prefixed_json(r.output, _SCENE_PREFIX) or {"error": "Scene-Inspector returned nothing"}

    def render(self, tag: str, num_views: int = 3) -> list[str]:
        """Render current_script from `num_views` angles; returns PNG paths."""
        if not self.current_script:
            return []
        out_dir = self.renders_dir / f"{tag}_{time.strftime('%H%M%S')}"
        script = _RENDER_SCRIPT.format(out_dir=str(out_dir), tag=tag, num_views=num_views,
                                       res=self.resolution, prefix=_RENDER_PREFIX)
        r = self.session.execute(self.current_script + "\n" + script, reset=True, timeout_s=300)
        if not r.ok:
            return []
        payload = _extract_prefixed_json(r.output, _RENDER_PREFIX) or {}
        return payload.get("renders", [])

    def as_tools(self) -> list[Tool]:
        return [
            Tool(
                name="execute_bpy",
                description=(
                    "Execute a COMPLETE Blender 4.4 Python (bpy) script in a fresh, EMPTY scene. "
                    "Each call starts from scratch, so always send the full script, not a diff. "
                    "Returns OK with a scene summary, or the error and traceback."
                ),
                parameters={"type": "object",
                            "properties": {"code": {"type": "string", "description": "Full bpy script."}},
                            "required": ["code"]},
                fn=self.execute_bpy,
            ),
            Tool(
                name="inspect_scene",
                description=(
                    "List every object produced by the last successful script: name, type, location, "
                    "bounding-box size, vertex count, materials, parent. Use it to check proportions "
                    "and that all requested parts exist."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
                fn=self.inspect_scene,
            ),
        ]

    def _scene_summary_line(self) -> str:
        snap = self.snapshot()
        if "error" in snap:
            return ""
        s = snap["summary"]
        names = [o["name"] for o in snap["objects"]][:15]
        return f"Scene now has {s['object_count']} objects ({s['mesh_object_count']} meshes): {names}"


def diff_snapshots(before: dict, after: dict, tol: float = 1e-4) -> dict:
    """What changed between two Scene-Inspector snapshots (used later by the Verifier)."""
    if "error" in before or "error" in after:
        return {"error": before.get("error") or after.get("error")}
    b = {o["name"]: o for o in before["objects"]}
    a = {o["name"]: o for o in after["objects"]}
    changed = []
    for name in sorted(b.keys() & a.keys()):
        delta = {}
        for key in ("location", "scale", "dimensions"):
            if any(abs(x - y) > tol for x, y in zip(b[name][key], a[name][key])):
                delta[key] = f"{b[name][key]} -> {a[name][key]}"
        if b[name]["material_names"] != a[name]["material_names"]:
            delta["materials"] = f"{b[name]['material_names']} -> {a[name]['material_names']}"
        if b[name]["vertex_count"] != a[name]["vertex_count"]:
            delta["vertices"] = f"{b[name]['vertex_count']} -> {a[name]['vertex_count']}"
        if delta:
            changed.append({"name": name, **delta})
    return {"added": sorted(a.keys() - b.keys()), "removed": sorted(b.keys() - a.keys()), "changed": changed}


def _extract_prefixed_json(stdout: str, prefix: str) -> Optional[dict]:
    for line in stdout.splitlines():
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):])
            except json.JSONDecodeError:
                return None
    return None


# Appended after the agent's script; prints one JSON line with the structured scene state.
_SCENE_INSPECTOR_SCRIPT = f"""
import bpy as _bpy, json as _json
from mathutils import Vector as _V
def _r(v): return [round(float(x), 4) for x in v]
def _bbox(o):
    c = [o.matrix_world @ _V(p) for p in o.bound_box]
    lo = _V((min(p.x for p in c), min(p.y for p in c), min(p.z for p in c)))
    hi = _V((max(p.x for p in c), max(p.y for p in c), max(p.z for p in c)))
    return {{"min": _r(lo), "max": _r(hi), "size": _r(hi - lo)}}
_objs = []
for _o in _bpy.context.scene.objects:
    _mesh = _o.type == 'MESH' and _o.data is not None
    _objs.append({{
        "name": _o.name, "type": _o.type, "parent": _o.parent.name if _o.parent else None,
        "location": _r(_o.location), "scale": _r(_o.scale), "dimensions": _r(_o.dimensions),
        "bbox": _bbox(_o) if _mesh else None,
        "vertex_count": len(_o.data.vertices) if _mesh else 0,
        "material_names": [s.material.name for s in _o.material_slots if s.material],
    }})
_objs.sort(key=lambda x: x["name"])
print("{_SCENE_PREFIX}" + _json.dumps({{"summary": {{
    "object_count": len(_objs),
    "mesh_object_count": sum(o["type"] == "MESH" for o in _objs),
    "total_vertex_count": sum(o["vertex_count"] for o in _objs)}}, "objects": _objs}}))
"""

# Appended after the agent's script; frames all meshes and renders N views around them.
_RENDER_SCRIPT = """
import bpy as _bpy, json as _json, math as _m, os as _os
from mathutils import Vector as _V
_out = r\"\"\"{out_dir}\"\"\"
_os.makedirs(_out, exist_ok=True)
_s = _bpy.context.scene
_s.render.engine = 'BLENDER_EEVEE_NEXT'
_s.render.resolution_x = _s.render.resolution_y = {res}
_s.render.image_settings.file_format = 'PNG'
if _s.world is None:
    _s.world = _bpy.data.worlds.new("BA_World")
_s.world.use_nodes = True
_bg = _s.world.node_tree.nodes.get("Background")
if _bg:
    _bg.inputs["Color"].default_value = (0.78, 0.80, 0.84, 1.0)
    _bg.inputs["Strength"].default_value = 0.9
_meshes = [o for o in _s.objects if o.type == 'MESH']
_paths = []
if _meshes:
    _pts = [o.matrix_world @ _V(c) for o in _meshes for c in o.bound_box]
    _lo = _V((min(p.x for p in _pts), min(p.y for p in _pts), min(p.z for p in _pts)))
    _hi = _V((max(p.x for p in _pts), max(p.y for p in _pts), max(p.z for p in _pts)))
    _center, _radius = (_lo + _hi) / 2, max((_hi - _lo).length / 2, 0.1)
    _cam = _bpy.data.objects.new("BA_Camera", _bpy.data.cameras.new("BA_Camera"))
    _s.collection.objects.link(_cam)
    _s.camera = _cam
    _cam.data.lens = 50
    if not any(o.type == 'LIGHT' for o in _s.objects):
        _sun = _bpy.data.objects.new("BA_Sun", _bpy.data.lights.new("BA_Sun", 'SUN'))
        _sun.data.energy = 3.0
        _sun.rotation_euler = (_m.radians(40), 0, _m.radians(35))
        _s.collection.objects.link(_sun)
    _dist = _radius * 3.0
    for _i in range({num_views}):
        _a = 2 * _m.pi * _i / {num_views} + _m.radians(30)
        _e = _m.radians(25)
        _cam.location = _center + _V((_dist * _m.cos(_a) * _m.cos(_e), _dist * _m.sin(_a) * _m.cos(_e), _dist * _m.sin(_e)))
        _cam.rotation_euler = (_center - _cam.location).to_track_quat('-Z', 'Y').to_euler()
        _s.render.filepath = _os.path.join(_out, "{tag}_%02d.png" % _i)
        _bpy.ops.render.render(write_still=True)
        _paths.append(_s.render.filepath)
print("{prefix}" + _json.dumps({{"renders": _paths}}))
"""
