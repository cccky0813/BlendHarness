"""Relation checks on a built scene: the subtler spatial problems a contact check cannot see.

The structural audit (analysis/structure.py) showed that floating or sunk parts are rare; most spatial issues
the Critic raises are relational: repeated parts unevenly spaced or sized, left/right parts that do not mirror
each other, a part resting on another with its centre off the support, a part stopping just short of what it
should touch. These checks find them from a Scene-Inspector snapshot and the part names alone (no Blender,
no LLM), so they can serve as an audit metric, an RL reward and an A/B metric alike.

Groups come from names: "Slat_1, Slat_2, Slat_3" are repeated parts (numeric suffix), "Rail_L / Rail_R" or
"Post_Front / Post_Back" are mirror pairs (one side token differs). Unnamed structure is not checked.
Axis-aligned bounding boxes only: rotated parts can be flagged wrongly, so every finding names its parts and
numbers for a human to verify.
"""

import re
import statistics
from collections import defaultdict
from typing import Any

from blendharness.analysis.structure import mesh_objects

SPACING_CV = 0.15          # spread of the gaps between repeated parts / their mean gap
SIZE_DEVIATION = 0.10      # relative deviation of a part's size from its group's median size
SAME = 0.02                # dimensions within 2% count as "the same design"
MIN_SUPPORT_AREA = 0.5     # supports must cover at least half the part's footprint to count as "resting on"
COLLINEAR = 0.10           # off-axis spread / on-axis spread below which a group counts as a row
PAIR_OFFSET = 0.02         # mirror pair: allowed offset on the two shared axes, as a fraction of the model size
NEAR_MISS = (0.01, 0.08)   # gap (m) between "touching" and "far": a part that stops just short of another
TOUCH = 0.002              # gap (m) that still counts as touching for the near-miss check
TOUCH_REST = 0.005         # gap (m) between a part's bottom and a support's top that counts as resting on it

_NUMBERED = re.compile(r"^(.*?)[_.\- ]?(\d+)$")
_SIDES = [("L", "R"), ("Left", "Right"), ("F", "B"), ("Front", "Back"), ("N", "S"), ("E", "W"),
          ("North", "South"), ("East", "West")]
_SIDE_OF = {s: i for i, pair in enumerate(_SIDES) for s in pair}


def _center(o: dict) -> list[float]:
    return [(o["bbox"]["min"][i] + o["bbox"]["max"][i]) / 2 for i in range(3)]


def _size(o: dict) -> list[float]:
    return o["bbox"]["size"]


def _gap(a: dict, b: dict) -> float:
    return max(max(b["bbox"]["min"][i] - a["bbox"]["max"][i], a["bbox"]["min"][i] - b["bbox"]["max"][i], 0.0)
               for i in range(3))


def numbered_groups(meshes: list[dict]) -> dict[str, list[dict]]:
    """Repeated parts: same name up to a trailing number (Slat_1, Slat_2, ...); 2 or more members."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for o in meshes:
        m = _NUMBERED.match(o["name"])
        if m and m.group(1):
            groups[m.group(1)].append(o)
    return {base: sorted(g, key=lambda o: int(_NUMBERED.match(o["name"]).group(2)))
            for base, g in groups.items() if len(g) >= 2}


def mirror_pairs(meshes: list[dict]) -> list[tuple[dict, dict]]:
    """Two parts whose names differ in exactly one side token (L/R, Front/Back, N/S, ...)."""
    by_key: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for o in meshes:
        tokens = re.split(r"[_.\- ]", o["name"])
        for i, t in enumerate(tokens):
            if t in _SIDE_OF:
                key = (tuple(tokens[:i]), _SIDE_OF[t], tuple(tokens[i + 1:]))
                by_key[key][t] = o
    pairs = []
    for (_, pair_id, _), sides in by_key.items():
        a, b = _SIDES[pair_id]
        if a in sides and b in sides:
            pairs.append((sides[a], sides[b]))
    return pairs


def _uneven_spacing(base: str, group: list[dict]) -> dict | None:
    if len(group) < 3:
        return None
    centers = [_center(o) for o in group]
    spread = [max(c[i] for c in centers) - min(c[i] for c in centers) for i in range(3)]
    axis = max(range(3), key=lambda i: spread[i])
    if spread[axis] == 0 or max(spread[i] for i in range(3) if i != axis) > COLLINEAR * spread[axis]:
        return None                                   # not a row (e.g. four corner legs): spacing undefined
    xs = sorted(c[axis] for c in centers)
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    mean = statistics.mean(gaps)
    cv = statistics.pstdev(gaps) / mean if mean > 0 else 0.0
    symmetric = all(_rel(g, h) <= 0.05 for g, h in zip(gaps, reversed(gaps)))   # e.g. two pairs at both ends
    if cv <= SPACING_CV or symmetric:
        return None
    return {"check": "uneven_spacing", "parts": [o["name"] for o in group],
            "detail": f"{base}: gaps along {'xyz'[axis]} = {[round(g, 3) for g in gaps]} (spread {cv:.0%} of the mean)"}


def _rel(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-6)


def _one_axis_off(d: list[float], ref: list[float]) -> float:
    """Deviation of `d` from `ref` if exactly one dimension differs (same design, one size wrong), else 0.
    Parts that differ in every dimension (rocks, books) are varied on purpose and not compared."""
    devs = [_rel(x, y) for x, y in zip(d, ref)]
    off = [x for x in devs if x > SAME]
    return off[0] if len(off) == 1 else 0.0


def _size_mismatch(base: str, group: list[dict]) -> list[dict]:
    dims = [sorted(_size(o)) for o in group]           # sorted: a part rotated by 90 degrees is the same size
    median = [statistics.median(d[i] for d in dims) for i in range(3)]
    if sum(max(_rel(x, y) for x, y in zip(d, median)) <= SAME for d in dims) * 2 <= len(dims):
        return []                                      # no majority design to compare against
    out = []
    for o, d in zip(group, dims):
        dev = _one_axis_off(d, median)
        if dev > SIZE_DEVIATION:
            out.append({"check": "size_mismatch", "parts": [o["name"]],
                        "detail": f"{o['name']} size {[round(x, 3) for x in _size(o)]} differs {dev:.0%} "
                                  f"from the other {base} parts"})
    return out


def _pair_checks(a: dict, b: dict, model_size: float) -> list[dict]:
    out = []
    da, db = sorted(_size(a)), sorted(_size(b))
    dev = _one_axis_off(da, db)
    if dev > SIZE_DEVIATION:
        out.append({"check": "pair_size_mismatch", "parts": [a["name"], b["name"]],
                    "detail": f"sizes {[round(x, 3) for x in _size(a)]} vs {[round(x, 3) for x in _size(b)]}"})
    ca, cb = _center(a), _center(b)
    delta = [abs(ca[i] - cb[i]) for i in range(3)]
    mirror_axis = max(range(3), key=lambda i: delta[i])
    off = max(delta[i] for i in range(3) if i != mirror_axis)
    same_design = max(_rel(x, y) for x, y in zip(da, db)) <= SIZE_DEVIATION
    if same_design and off > max(PAIR_OFFSET * model_size, 0.01):   # only parts built alike must mirror
        out.append({"check": "pair_misaligned", "parts": [a["name"], b["name"]],
                    "detail": f"mirrored along {'xyz'[mirror_axis]} but offset {off:.3f} m on another axis"})
    return out


def _off_support(meshes: list[dict], gap: float) -> list[dict]:
    """A part sitting on top of another with its footprint centre outside the supporting top face."""
    out = []
    for a in meshes:
        bottom = a["bbox"]["min"][2]
        if bottom <= gap:                              # on the ground
            continue
        supports = [b for b in meshes if b is not a and abs(b["bbox"]["max"][2] - bottom) <= gap
                    and all(a["bbox"]["min"][i] < b["bbox"]["max"][i] and b["bbox"]["min"][i] < a["bbox"]["max"][i]
                            for i in range(2))]
        if not supports:
            continue
        foot = a["bbox"]["size"][0] * a["bbox"]["size"][1]
        if sum(b["bbox"]["size"][0] * b["bbox"]["size"][1] for b in supports) < MIN_SUPPORT_AREA * foot:
            continue                                   # sits on an edge (bracket arm, reclined slat): not resting
        cx, cy = _center(a)[:2]
        # all supports together (a table top on four legs): the centre must lie within their combined footprint
        x0, x1 = min(b["bbox"]["min"][0] for b in supports), max(b["bbox"]["max"][0] for b in supports)
        y0, y1 = min(b["bbox"]["min"][1] for b in supports), max(b["bbox"]["max"][1] for b in supports)
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            out.append({"check": "off_support", "parts": [a["name"]] + [b["name"] for b in supports[:2]],
                        "detail": f"{a['name']} rests on {supports[0]['name']} with its centre outside the support"})
    return out


def _near_misses(meshes: list[dict], sibling: dict[str, str]) -> list[dict]:
    """A part that touches nothing but stops 1-8 cm short of another: an intended joint that was missed.
    Siblings (same name up to its numbers: slats, rungs, grid tiles) are spaced on purpose, so they are not
    candidate joints."""
    out = []
    for a in meshes:
        gaps = [(_gap(a, b), b) for b in meshes
                if b is not a and not (a["name"] in sibling and sibling.get(b["name"]) == sibling[a["name"]])]
        if not gaps or min(g for g, _ in gaps) <= TOUCH:
            continue
        g, b = min(gaps, key=lambda x: x[0])
        if NEAR_MISS[0] < g <= NEAR_MISS[1]:
            out.append({"check": "near_miss", "parts": [a["name"], b["name"]],
                        "detail": f"{a['name']} stops {g * 100:.1f} cm short of {b['name']}"})
    return out


def check_relations(snapshot: dict) -> dict[str, Any]:
    meshes = mesh_objects(snapshot)
    if len(meshes) < 2:
        return {"findings": [], "groups": 0, "pairs": 0}
    lo = [min(o["bbox"]["min"][i] for o in meshes) for i in range(3)]
    hi = [max(o["bbox"]["max"][i] for o in meshes) for i in range(3)]
    model_size = max(hi[i] - lo[i] for i in range(3)) or 1.0
    findings: list[dict] = []
    groups = numbered_groups(meshes)
    for base, group in groups.items():
        spacing = _uneven_spacing(base, group)
        findings += [spacing] if spacing else []
        findings += _size_mismatch(base, group)
    pairs = mirror_pairs(meshes)
    for a, b in pairs:
        findings += _pair_checks(a, b, model_size)
    findings += _off_support(meshes, TOUCH_REST)
    findings += _near_misses(meshes, {o["name"]: re.sub(r"\d+", "#", o["name"]) for o in meshes
                                      if re.search(r"\d", o["name"])})
    return {"findings": findings, "groups": len(groups), "pairs": len(pairs)}
