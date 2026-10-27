"""Turn expectations: each check kind, undecidable patterns, regressions and preconditions (no Blender)."""

import json
from pathlib import Path

from blendharness.analysis.expectations import (FAIL, PASS, UNDECIDABLE, KINDS, CheckResult, evaluate, hue_name,
                                                instance_key, kind_of, regressions, score_turn)


def obj(name, lo, hi, colors=None, verts=8):
    return {"name": name, "type": "MESH", "parent": None, "location": [0, 0, 0], "vertex_count": verts,
            "dimensions": [h - l for l, h in zip(lo, hi)], "material_names": [f"M_{c}" for c in (colors or [])],
            "material_colors": [c for c in (colors or [])],
            "bbox": {"min": list(lo), "max": list(hi), "size": [h - l for l, h in zip(lo, hi)]}}


RED, GREY = [0.8, 0.05, 0.03], [0.5, 0.5, 0.5]
VILLAGE = {"objects": [
    obj("Ground", (-5, -5, -0.05), (5, 5, 0)),
    obj("Cottage_Walls", (-1, -1, 0), (1, 1, 2)),
    obj("Cottage_Roof", (-1.1, -1.1, 2), (1.1, 1.1, 3), colors=[RED]),
    obj("Cottage_Door", (0.2, -1.02, 0), (0.7, -1.0, 1.6)),
    obj("Well_Ring", (3, -3, 0), (4, -2, 0.8), colors=[GREY]),
    obj("Well_Bucket", (4.0, -2.6, 0), (4.3, -2.3, 0.35)),
    obj("Tree_1", (-4, 2, 0), (-3, 3, 3)), obj("Tree_2", (-2, 2, 0), (-1, 3, 3)), obj("Tree_3", (0, 2, 0), (1, 3, 3)),
    obj("Lamp_Shade", (3.2, -2.8, 0.8), (3.5, -2.5, 1.1)),
]}


def ev(check):
    return evaluate({"id": "c", **check}, VILLAGE)


def test_exists_absent_count():
    assert ev({"exists": "well"}).status == PASS
    assert ev({"exists": "hydrant"}).status == FAIL
    assert ev({"absent": "fence"}).status == PASS and ev({"absent": "tree"}).status == FAIL
    assert ev({"count": "tree", "min": 3, "max": 3}).status == PASS
    assert ev({"count": "tree", "min": 4}).status == FAIL


def test_count_counts_instances_not_parts():
    stand = lambda side, x: [obj(f"Nightstand_{side}_Body", (x, 0, 0), (x + 0.5, 0.4, 0.5)),     # noqa: E731
                             obj(f"Nightstand_{side}_Top", (x, 0, 0.5), (x + 0.5, 0.4, 0.55)),
                             obj(f"Nightstand_{side}_Knob", (x + 0.2, -0.03, 0.3), (x + 0.3, 0, 0.33))]
    one = {"objects": stand("L", 0)}
    two = {"objects": stand("L", 0) + stand("R", 2)}
    assert evaluate({"id": "c", "count": "nightstand", "min": 2}, one).status == FAIL
    assert evaluate({"id": "c", "count": "nightstand", "min": 2}, two).status == PASS
    # parts that do not touch (a drawer front with a reveal) still belong to their instance by name
    gap_parts = {"objects": [obj("Nightstand_L_Body", (0, 0, 0), (0.5, 0.4, 0.5)),
                             obj("Nightstand_L_Drawer_1", (0.05, -0.03, 0.1), (0.45, -0.01, 0.2)),
                             obj("Nightstand_R_Body", (2, 0, 0), (2.5, 0.4, 0.5))]}
    assert evaluate({"id": "c", "count": "nightstand", "min": 2, "max": 2}, gap_parts).status == PASS


def test_instance_keys():
    assert instance_key("Nightstand_L_Drawer_1") == instance_key("Nightstand_L_Top") == "side:l"
    assert instance_key("Lamp_Base_R") == "side:r"
    assert instance_key("Cottage_WindowFrame_2") == instance_key("Cottage_WindowGlass_2") == "num:2"
    assert instance_key("Tree2_Fol0") == "num:2" and instance_key("Tree_Pine_Trunk") is None
    # pilot 2026-10-27: row-and-plant numbering; a carrot's root and top are still one plant
    assert instance_key("Garden_Cabbage_0_3") == "num:0_3" != instance_key("Garden_Cabbage_0_4")
    assert instance_key("Garden_Carrot_1_0_Root") == instance_key("Garden_Carrot_1_0_Top") == "num:1_0"


def test_real_naming_styles_count_right():
    trees = {"objects": [obj("Tree_Pine_Trunk", (0, 0, 0), (0.2, 0.2, 1)), obj("Tree_Pine_Foliage", (-0.5, -0.5, 1), (0.7, 0.7, 3)),
                         obj("Tree_Round_Trunk", (4, 0, 0), (4.2, 0.2, 1)), obj("Tree_Round_Foliage", (3.5, -0.5, 1), (4.7, 0.7, 2.5))]}
    windows = {"objects": [obj("Cottage_WindowFrame_1", (0, 0, 1), (0.6, 0.05, 1.6)), obj("Cottage_WindowGlass_1", (0.05, 0.01, 1.05), (0.55, 0.04, 1.55)),
                           obj("Cottage_WindowFrame_2", (2, 0, 1), (2.6, 0.05, 1.6)), obj("Cottage_WindowGlass_2", (2.05, 0.01, 1.05), (2.55, 0.04, 1.55))]}
    assert evaluate({"id": "c", "count": "tree|trunk", "min": 2, "max": 2}, trees).status == PASS
    assert evaluate({"id": "c", "count": "window", "min": 2, "max": 2}, windows).status == PASS


def test_relations():
    assert ev({"near": ["bucket", "well_ring"], "max_gap": 0.05}).status == PASS
    assert ev({"near": ["tree_1", "well"], "max_gap": 0.05}).status == FAIL
    assert ev({"on": ["lamp_shade", "well_ring"]}).status == PASS
    assert ev({"on": ["roof", "walls"]}).status == PASS
    assert ev({"above": ["roof", "door"]}).status == PASS
    assert ev({"direction": ["well", "cottage"], "dir": "east", "min": 2, "max": 5}).status == PASS
    assert ev({"direction": ["well", "cottage"], "dir": "north"}).status == FAIL
    assert ev({"contains_xy": ["ground", "cottage"]}).status == PASS
    assert ev({"grounded": "bucket"}).status == PASS and ev({"grounded": "lamp"}).status == FAIL
    assert ev({"evenly_spaced": "tree", "tolerance": 0.1}).status == PASS


def test_unmatched_pattern_is_undecidable_not_failed():
    assert ev({"near": ["barrel", "well"], "max_gap": 0.05}).status == UNDECIDABLE
    assert ev({"grounded": "hydrant"}).status == UNDECIDABLE
    assert ev({"evenly_spaced": "door"}).status == UNDECIDABLE


def test_material_hue():
    assert ev({"material_hue": "roof", "hue": "red"}).status == PASS
    assert ev({"material_hue": "well", "hue": "red"}).status == FAIL          # grey has no hue
    assert ev({"material_hue": "tree", "hue": "green"}).status == UNDECIDABLE  # no plain base color
    assert hue_name([0.9, 0.8, 0.1]) == "yellow" and hue_name([0.1, 0.2, 0.8]) == "blue" and hue_name(GREY) is None


def test_regressions_outside_touches():
    after = json.loads(json.dumps(VILLAGE))
    for o in after["objects"]:
        if o["name"] == "Cottage_Roof":
            o["material_colors"] = [[0.1, 0.1, 0.9]]                         # allowed: the turn is about the roof
        if o["name"] == "Tree_3":
            o["bbox"]["min"][0] += 0.5; o["bbox"]["max"][0] += 0.5            # not allowed
    after["objects"] = [o for o in after["objects"] if o["name"] != "Well_Bucket"]
    after["objects"].append(obj("Stray_Rock", (2, 2, 0), (2.3, 2.3, 0.2)))
    r = regressions(VILLAGE, after, ["roof"])
    changes = {x["object"]: x["change"] for x in r["regressions"]}
    assert changes == {"Well_Bucket": "removed", "Stray_Rock": "added (not requested)", "Tree_3": "moved/resized"}
    assert r["checked"] == len(VILLAGE["objects"]) - 1
    assert regressions(VILLAGE, after, ["*"])["regressions"] == []


def test_renames_are_reported_apart():
    after = json.loads(json.dumps(VILLAGE))
    for o in after["objects"]:
        if o["name"] == "Well_Bucket":
            o["name"] = "Bucket"
    r = regressions(VILLAGE, after, ["roof"])
    assert r["regressions"] == [] and r["renamed"] == [("Well_Bucket", "Bucket")]


def test_score_turn_preconditions_and_completion():
    turn = {"expect": [{"id": "t_red", "material_hue": "roof", "hue": "red"}], "requires": ["v1_house"], "touches": ["roof"]}
    ok = score_turn(turn, VILLAGE, VILLAGE, {"v1_house": CheckResult("v1_house", "exists", PASS)})
    assert ok["completed"] and ok["precondition_failed"] == [] and ok["regressions"] == []
    bad = score_turn(turn, VILLAGE, VILLAGE, {"v1_house": CheckResult("v1_house", "exists", FAIL)})
    assert bad["precondition_failed"] == ["v1_house"]


def test_draft_sessions_use_only_known_check_kinds():
    data = json.loads((Path(__file__).resolve().parents[2] / "eval" / "sessions_draft.json").read_text())
    kinds = {kind_of(c) for s in data["sessions"] for t in s["turns"] for c in t["expect"]}
    assert kinds <= set(KINDS)


def test_contains_xy_sides_only_needs_the_narrow_axis():
    """'A rug that sticks out on both sides of the bed': the bed's head is against the wall (pilot 2026-10-27)."""
    snap = {"objects": [obj("Rug", (-1.3, 0.45, 0), (1.3, 2.5, 0.01)), obj("Bed", (-0.85, 0.92, 0), (0.85, 2.98, 0.6))]}
    assert evaluate({"id": "c", "contains_xy": ["rug", "bed"]}, snap).status == "fail"
    assert evaluate({"id": "c", "contains_xy": ["rug", "bed"], "sides": True}, snap).status == "pass"
    narrow = {"objects": [obj("Rug", (-0.5, 0, 0), (0.5, 3, 0.01)), obj("Bed", (-0.85, 0.92, 0), (0.85, 2.98, 0.6))]}
    assert evaluate({"id": "c", "contains_xy": ["rug", "bed"], "sides": True}, narrow).status == "fail"
