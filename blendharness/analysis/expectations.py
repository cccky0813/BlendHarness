"""Expectations: what one turn of a multi-turn session should have changed, checked on Scene-Inspector snapshots.

A session file (eval/sessions_*.json) gives every turn a list of checks written in advance:

    {"id": "v6_touch_well", "near": ["bucket|barrel", "well"], "max_gap": 0.05}

Patterns are case-insensitive regexes over object names, because names are chosen by the Coder (the audit found
them meaningful in practice). A check whose pattern matches nothing cannot say anything about a relation, so it
is "undecidable" rather than failed; only `exists` / `count` fail when nothing matches, since absence is exactly
what they test. Axes: +x east, +y north, z up, ground at z = 0. Pure Python, no Blender, no LLM.

`regressions()` compares the scene before and after a turn: any object outside the turn's `touches` patterns
that was removed, added, moved, resized or recoloured is a regression (renames are reported apart).
"""

import colorsys
import re
import statistics
from dataclasses import dataclass
from typing import Any, Optional

from blendharness.analysis.structure import mesh_objects

PASS, FAIL, UNDECIDABLE = "pass", "fail", "undecidable"
GROUND_TOL = 0.05          # m: lowest point within this of z = 0 counts as on the ground
REST_TOL = 0.05            # m: bottom of A within this of the top of B counts as resting on it
MOVE_TOL = 0.01            # m: a bounding-box corner moving more than this is a change
SIZE_TOL = 0.01            # relative: a dimension changing more than this is a change
INSTANCE_GAP = 0.005       # m: matching parts closer than this belong to the same instance (for `count`)
HUES = {"red": (345, 15), "orange": (15, 40), "yellow": (35, 70), "green": (70, 170),
        "blue": (170, 260), "purple": (260, 345)}   # degrees; red wraps around 0
MIN_SATURATION = 0.25      # greys, whites and blacks have no hue

KINDS = ("exists", "absent", "count", "near", "on", "above", "direction", "grounded", "contains_xy",
         "evenly_spaced", "material_hue")


@dataclass
class CheckResult:
    id: str
    kind: str
    status: str            # pass | fail | undecidable
    detail: str = ""


def kind_of(check: dict) -> str:
    return next((k for k in KINDS if k in check), "unknown")


def match(pattern: str, objects: list[dict]) -> list[dict]:
    rx = re.compile(pattern, re.IGNORECASE)
    return [o for o in objects if rx.search(o["name"])]


_TOKENS = re.compile(r"[_.\-\s]+|(?<=[a-z])(?=[A-Z])")
_SIDES = {"l", "r", "left", "right", "f", "b", "front", "back", "n", "s", "e", "w", "north", "south", "east", "west"}


def instance_key(name: str) -> Optional[str]:
    """What tells this part's instance apart in its name: its side tokens ('Lamp_Base_L', 'Nightstand_L_Drawer_1'
    -> 'l'), else its first run of number tokens ('Window_Frame_2' -> '2', 'Cabbage_0_3' -> '0_3': row 0, plant 3;
    pilot 2026-10-27, taking only the row merged a whole row), else the first number glued to a word
    ('Tree2_Fol0' -> '2'), else None (then only touching groups parts). Part indices after a side ('Drawer_1') are
    ignored, since the side already names the instance."""
    tokens = [t.lower() for t in _TOKENS.split(name) if t]
    sides = [t for t in tokens if t in _SIDES]
    if sides:
        return "side:" + "_".join(sides)
    run = []
    for t in tokens:
        if t.isdigit():
            run.append(str(int(t)))
        elif run:
            break
    if run:
        return "num:" + "_".join(run)
    num = re.search(r"\d+", name)
    return "num:" + str(int(num.group(0))) if num else None


def instances(objs: list[dict]) -> list[list[dict]]:
    """Group matching parts into instances: parts that touch, or that share an instance key, are one instance."""
    parent = list(range(len(objs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    keys = [instance_key(o["name"]) for o in objs]
    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            if (keys[i] is not None and keys[i] == keys[j]) or _gap(objs[i], objs[j]) <= INSTANCE_GAP:
                parent[find(i)] = find(j)
    groups: dict[int, list[dict]] = {}
    for i, o in enumerate(objs):
        groups.setdefault(find(i), []).append(o)
    return list(groups.values())


def _union(objs: list[dict]) -> dict:
    lo = [min(o["bbox"]["min"][i] for o in objs) for i in range(3)]
    hi = [max(o["bbox"]["max"][i] for o in objs) for i in range(3)]
    return {"min": lo, "max": hi, "center": [(a + b) / 2 for a, b in zip(lo, hi)]}


def _gap(a: dict, b: dict) -> float:
    return max(max(b["bbox"]["min"][i] - a["bbox"]["max"][i], a["bbox"]["min"][i] - b["bbox"]["max"][i], 0.0)
               for i in range(3))


def _xy_overlap(a: dict, b: dict) -> bool:
    return all(a["min"][i] < b["max"][i] and b["min"][i] < a["max"][i] for i in range(2))


def hue_name(rgb: list[float]) -> Optional[str]:
    h, s, v = colorsys.rgb_to_hsv(*[max(0.0, min(1.0, c)) for c in rgb])
    if s < MIN_SATURATION or v < 0.03:
        return None
    deg = h * 360
    for name, (lo, hi) in HUES.items():
        if (lo <= deg < hi) if lo < hi else (deg >= lo or deg < hi):
            return name
    return None


def _in_hue(rgb: list[float], hue: str) -> bool:
    h, s, v = colorsys.rgb_to_hsv(*[max(0.0, min(1.0, c)) for c in rgb])
    lo, hi = HUES[hue]
    deg = h * 360
    return s >= MIN_SATURATION and v >= 0.03 and ((lo <= deg < hi) if lo < hi else (deg >= lo or deg < hi))


def evaluate(check: dict, snapshot: dict) -> CheckResult:
    objs = mesh_objects(snapshot)
    kind, cid = kind_of(check), check.get("id", "?")

    def res(status: str, detail: str = "") -> CheckResult:
        return CheckResult(cid, kind, status, detail)

    def groups(n: int) -> Optional[list[list[dict]]]:
        pats = check[kind] if n > 1 else [check[kind]]
        found = [match(p, objs) for p in pats]
        return None if any(not g for g in found) else found

    if kind == "exists":
        m = match(check["exists"], objs)
        return res(PASS if m else FAIL, f"{len(m)} match(es)")
    if kind == "absent":
        m = match(check["absent"], objs)
        return res(FAIL if m else PASS, ", ".join(o["name"] for o in m[:5]))
    if kind == "count":
        # Count instances, not objects: one nightstand is often a dozen parts (Nightstand_L_Top, _Drawer...).
        # Matching parts that touch each other form one instance.
        m = match(check["count"], objs)
        n = len(instances(m))
        ok = n >= check.get("min", 0) and n <= check.get("max", float("inf"))
        return res(PASS if ok else FAIL, f"{n} instance(s) from {len(m)} object(s)")

    if kind in ("near", "on", "above", "direction", "contains_xy"):
        g = groups(2)
        if g is None:
            return res(UNDECIDABLE, "a pattern matched no object")
        a_objs, b_objs = g
        b_objs = [b for b in b_objs if b["name"] not in {a["name"] for a in a_objs}] or b_objs
        a, b = _union(a_objs), _union(b_objs)
        if kind == "near":
            gap = min(_gap(x, y) for x in a_objs for y in b_objs if x["name"] != y["name"])
            return res(PASS if gap <= check.get("max_gap", 0.05) else FAIL, f"gap {gap:.3f} m")
        if kind == "on":
            lowest = [x for x in a_objs if x["bbox"]["min"][2] <= a["min"][2] + REST_TOL]
            ok = all(any(abs(x["bbox"]["min"][2] - y["bbox"]["max"][2]) <= REST_TOL and _xy_overlap(
                {"min": x["bbox"]["min"], "max": x["bbox"]["max"]}, {"min": y["bbox"]["min"], "max": y["bbox"]["max"]})
                for y in b_objs) for x in lowest)
            return res(PASS if ok else FAIL, f"bottom {a['min'][2]:.3f}, support top {b['max'][2]:.3f}")
        if kind == "above":
            ok = a["min"][2] >= b["max"][2] - REST_TOL and _xy_overlap(a, b)
            return res(PASS if ok else FAIL, f"bottom {a['min'][2]:.3f} vs top {b['max'][2]:.3f}")
        if kind == "direction":
            axis, sign = {"east": (0, 1), "west": (0, -1), "north": (1, 1), "south": (1, -1)}[check["dir"]]
            d = sign * (a["center"][axis] - b["center"][axis])
            ok = check.get("min", 0.0) <= d <= check.get("max", float("inf"))
            return res(PASS if ok else FAIL, f"{d:.2f} m {check['dir']}")
        if kind == "contains_xy":
            covers = lambda i: a["min"][i] <= b["min"][i] + 0.02 and a["max"][i] >= b["max"][i] - 0.02  # noqa: E731
            if check.get("sides"):     # "sticks out on both SIDES": across B's narrow axis only, overlapping along it
                narrow = 0 if (b["max"][0] - b["min"][0]) <= (b["max"][1] - b["min"][1]) else 1
                ok = covers(narrow) and _xy_overlap(a, b)
            else:
                ok = covers(0) and covers(1)
            return res(PASS if ok else FAIL, f"A {a['min'][:2]}..{a['max'][:2]} vs B {b['min'][:2]}..{b['max'][:2]}")

    if kind == "grounded":
        g = groups(1)
        if g is None:
            return res(UNDECIDABLE, "pattern matched no object")
        z = _union(g[0])["min"][2]
        return res(PASS if -GROUND_TOL <= z <= GROUND_TOL else FAIL, f"lowest z {z:.3f}")

    if kind == "evenly_spaced":
        g = groups(1)
        if g is None or len(g[0]) < 3:
            return res(UNDECIDABLE, "fewer than 3 matches")
        centers = [[(o["bbox"]["min"][i] + o["bbox"]["max"][i]) / 2 for i in range(3)] for o in g[0]]
        axis = max(range(2), key=lambda i: max(c[i] for c in centers) - min(c[i] for c in centers))
        xs = sorted(c[axis] for c in centers)
        gaps = [q - p for p, q in zip(xs, xs[1:])]
        cv = statistics.pstdev(gaps) / statistics.mean(gaps) if statistics.mean(gaps) > 0 else float("inf")
        return res(PASS if cv <= check.get("tolerance", 0.15) else FAIL, f"gaps {[round(x, 2) for x in gaps]}")

    if kind == "material_hue":
        g = groups(1)
        if g is None:
            return res(UNDECIDABLE, "pattern matched no object")
        known = [o for o in g[0] if any(c for c in o.get("material_colors") or [])]
        if not known:
            return res(UNDECIDABLE, "no plain base color (texture-driven or no material)")
        hits = sum(any(c and _in_hue(c, check["hue"]) for c in o["material_colors"]) for o in known)
        return res(PASS if hits * 2 > len(known) else FAIL, f"{hits}/{len(known)} objects {check['hue']}")

    return res(UNDECIDABLE, f"unknown check kind in {sorted(check)}")


def _changed(a: dict, b: dict) -> list[str]:
    what = []
    if a.get("bbox") and b.get("bbox"):
        if any(abs(x - y) > MOVE_TOL for k in ("min", "max") for x, y in zip(a["bbox"][k], b["bbox"][k])):
            what.append("moved/resized")
    elif a.get("dimensions") and b.get("dimensions") and any(
            abs(x - y) > SIZE_TOL * max(abs(x), 1e-6) for x, y in zip(a["dimensions"], b["dimensions"])):
        what.append("resized")
    if a.get("material_names") != b.get("material_names") or a.get("material_colors") != b.get("material_colors"):
        what.append("material")
    if a.get("vertex_count") != b.get("vertex_count"):
        what.append("geometry")
    return what


def regressions(before: dict, after: dict, touches: list[str]) -> dict[str, Any]:
    """Changes outside the patterns this turn was allowed to touch."""
    if "*" in touches:
        return {"checked": 0, "regressions": [], "renamed": []}
    allowed = re.compile("|".join(f"(?:{p})" for p in touches), re.IGNORECASE) if touches else None
    free = lambda o: bool(allowed and allowed.search(o["name"]))  # noqa: E731
    b = {o["name"]: o for o in before.get("objects", [])}
    a = {o["name"]: o for o in after.get("objects", [])}
    protected = [n for n in b if not free(b[n])]
    removed = [n for n in protected if n not in a]
    added = [n for n in a if n not in b and not free(a[n])]
    renamed = []
    for r in list(removed):             # same box, same vertex count under a new name: a rename, reported apart
        twin = next((n for n in added if not {"moved/resized", "resized", "geometry"} & set(_changed(b[r], a[n]))), None)
        if twin:
            renamed.append((r, twin)); removed.remove(r); added.remove(twin)
    out = [{"object": n, "change": "removed"} for n in removed]
    out += [{"object": n, "change": "added (not requested)"} for n in added]
    out += [{"object": n, "change": ", ".join(c)} for n in protected if n in a for c in [_changed(b[n], a[n])] if c]
    return {"checked": len(protected), "regressions": out, "renamed": renamed}


def score_turn(turn: dict, before: dict, after: dict, earlier: dict[str, CheckResult]) -> dict[str, Any]:
    """One turn: its own checks, whether its preconditions held, and regressions against the scene before it."""
    results = [evaluate(c, after) for c in turn.get("expect", [])]
    missing = [r for r in turn.get("requires", []) if earlier.get(r) is None or earlier[r].status != PASS]
    decided = [r for r in results if r.status != UNDECIDABLE]
    reg = regressions(before, after, turn.get("touches", []))
    return {
        "results": results,
        "precondition_failed": missing,
        "completed": bool(decided) and all(r.status == PASS for r in decided) and len(decided) == len(results),
        "undecidable": sum(r.status == UNDECIDABLE for r in results),
        "regressions": reg["regressions"], "renamed": reg["renamed"], "protected_objects": reg["checked"],
    }
