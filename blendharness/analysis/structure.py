"""Structural audit of a built scene: is every part supported, is anything sunk, can parts be addressed by name.

Pure geometry on a Scene-Inspector snapshot (tools/scene.py); no Blender, no LLM. It answers two questions
without a judge model:
    defects        parts hanging in the air (a group of touching parts that does not reach the ground) and
                   parts sunk below the ground (z < 0)
    addressability can a later step move or resize parts by name (separate mesh objects, meaningful names)?

Axis-aligned bounding boxes only, with the same relative tolerances as the skill admission gate
(workflow/learning/admission.py). Boxes that touch count as connected, so a diagonal part can look attached
when it is not: the floating count is a LOWER bound, not a proof.
"""

import re
from typing import Any

GROUND_TOLERANCE = 0.02    # fraction of the scene's largest extent allowed below z = 0 (as in admission.py)
CONTACT_TOLERANCE = 0.02   # fraction of the scene's largest extent allowed as a gap between touching parts
MIN_TOLERANCE = 0.01       # at least 1 cm
HEAVY_OVERLAP = 0.5        # overlap volume / smaller box volume above which a pair counts as heavily overlapping

# Blender's default object names: a part called "Cube.003" cannot be told apart from its siblings by name.
DEFAULT_NAME = re.compile(r"^(Cube|Cylinder|Sphere|UVSphere|Icosphere|Cone|Torus|Plane|Circle|Grid|Suzanne|Mesh|Object)"
                          r"(\.\d+)?$")


def mesh_objects(snapshot: dict) -> list[dict]:
    return [o for o in snapshot.get("objects", []) if o.get("type") == "MESH" and o.get("bbox")]


def _touch(a: dict, b: dict, gap: float) -> bool:
    return all(a["bbox"]["min"][i] - gap <= b["bbox"]["max"][i] and b["bbox"]["min"][i] - gap <= a["bbox"]["max"][i]
               for i in range(3))


def components(meshes: list[dict], gap: float) -> list[list[dict]]:
    """Groups of meshes connected through touching bounding boxes (flood fill), largest first."""
    left, groups = list(meshes), []
    while left:
        group = [left.pop(0)]
        frontier = list(group)
        while frontier:
            cur = frontier.pop()
            touching = [o for o in left if _touch(cur, o, gap)]
            for o in touching:
                left.remove(o)
            group += touching
            frontier += touching
        groups.append(group)
    return sorted(groups, key=len, reverse=True)


def _gap(a: dict, b: dict) -> float:
    """Distance between two boxes along the axis where they are furthest apart (0 if they overlap)."""
    return max(max(b["bbox"]["min"][i] - a["bbox"]["max"][i], a["bbox"]["min"][i] - b["bbox"]["max"][i], 0.0)
               for i in range(3))


def _overlap_share(a: dict, b: dict) -> float:
    inter, vols = 1.0, []
    for o in (a, b):
        size = o["bbox"]["size"]
        vols.append(max(size[0], 1e-9) * max(size[1], 1e-9) * max(size[2], 1e-9))
    for i in range(3):
        inter *= max(0.0, min(a["bbox"]["max"][i], b["bbox"]["max"][i]) - max(a["bbox"]["min"][i], b["bbox"]["min"][i]))
    return inter / min(vols)


def audit(snapshot: dict) -> dict[str, Any]:
    meshes = mesh_objects(snapshot)
    if not meshes:
        return {"meshes": 0, "defect": False, "error": "no mesh with a bounding box"}
    lo = [min(o["bbox"]["min"][i] for o in meshes) for i in range(3)]
    hi = [max(o["bbox"]["max"][i] for o in meshes) for i in range(3)]
    size = max(hi[i] - lo[i] for i in range(3)) or 1.0
    ground_tol = max(GROUND_TOLERANCE * size, MIN_TOLERANCE)
    gap = max(CONTACT_TOLERANCE * size, MIN_TOLERANCE)

    groups = components(meshes, gap)
    grounded = [g for g in groups if min(o["bbox"]["min"][2] for o in g) <= ground_tol]
    # Nothing reaches the ground (e.g. a wall-mounted cabinet): the largest group is the model, the rest float.
    floating = [g for g in groups if g not in grounded] if grounded else groups[1:]
    # A part entirely at or below ground level (floor slab, patio, pavers, riverbed) is ground, not sunk;
    # sunk = sticks out of the ground but its bottom goes below it.
    sunk = sorted((o for o in meshes if o["bbox"]["min"][2] < -ground_tol and o["bbox"]["max"][2] > ground_tol),
                  key=lambda o: o["bbox"]["min"][2])
    heavy = sum(_overlap_share(a, b) > HEAVY_OVERLAP for i, a in enumerate(meshes) for b in meshes[i + 1:])
    return {
        "meshes": len(meshes),
        "components": len(groups),
        "floating_components": len(floating),
        "floating_parts": [o["name"] for g in floating for o in g],
        # per floating group: distance to the nearest other part (a few cm = detached; ~1 m = nothing to hang on)
        "floating_gaps": [round(min(_gap(a, b) for a in g for b in meshes if b not in g), 3) for g in floating],
        "sunk_parts": [o["name"] for o in sunk],
        "lowest_z": round(lo[2], 4),
        "elevated": not grounded,                      # the whole model is off the ground
        "default_named": sum(bool(DEFAULT_NAME.match(o["name"])) for o in meshes),
        "parented": sum(bool(o.get("parent")) for o in meshes),
        "heavy_overlaps": heavy,                       # informational: containment (liquid in a mug) is legitimate
        "defect": bool(floating or sunk),
    }
