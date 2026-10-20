"""Scene state: the Scene-Inspector script that runs inside Blender, and helpers to read and compare its output."""

import json
from typing import Optional

SCENE_PREFIX = "BA_SCENE_RESULT="


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


def extract_prefixed_json(stdout: str, prefix: str) -> Optional[dict]:
    for line in stdout.splitlines():
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):])
            except json.JSONDecodeError:
                return None
    return None


# Appended after the agent's script; prints one JSON line with the structured scene state.
SCENE_INSPECTOR_SCRIPT = f"""
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
print("{SCENE_PREFIX}" + _json.dumps({{"summary": {{
    "object_count": len(_objs),
    "mesh_object_count": sum(o["type"] == "MESH" for o in _objs),
    "total_vertex_count": sum(o["vertex_count"] for o in _objs)}}, "objects": _objs}}))
"""
