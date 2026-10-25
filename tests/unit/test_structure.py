"""Structural audit: floating groups, sunk parts, separate objects and names, on hand-made snapshots."""

from blendharness.analysis.structure import audit, components


def box(name: str, lo: tuple, hi: tuple, parent: str | None = None) -> dict:
    return {"name": name, "type": "MESH", "parent": parent,
            "bbox": {"min": list(lo), "max": list(hi), "size": [h - l for l, h in zip(lo, hi)]}}


def chair(backrest_z: float = 0.45) -> list[dict]:
    legs = [box(f"Leg_{i}", (x, y, 0), (x + 0.04, y + 0.04, 0.43)) for i, (x, y) in
            enumerate([(-0.2, -0.2), (0.16, -0.2), (-0.2, 0.16), (0.16, 0.16)])]
    return legs + [box("Seat", (-0.2, -0.2, 0.43), (0.2, 0.2, 0.47)),
                   box("Backrest", (-0.2, 0.17, backrest_z), (0.2, 0.2, backrest_z + 0.45))]


def test_sound_chair_has_no_defect():
    r = audit({"objects": chair()})
    assert (r["meshes"], r["components"], r["defect"]) == (6, 1, False)
    assert r["floating_parts"] == [] and r["sunk_parts"] == [] and not r["elevated"]


def test_floating_backrest_is_found():
    r = audit({"objects": chair(backrest_z=0.7)})          # 23 cm above the seat
    assert r["defect"] and r["floating_parts"] == ["Backrest"] and r["floating_components"] == 1


def test_sunk_part_is_found():
    objs = chair()
    objs[0] = box("Leg_0", (-0.2, -0.2, -0.1), (-0.16, -0.16, 0.43))   # pokes 10 cm into the ground
    r = audit({"objects": objs})
    assert r["defect"] and r["sunk_parts"] == ["Leg_0"]


def test_ground_slabs_below_zero_are_not_sunk():
    floor = box("Floor", (-2, -2, -0.1), (2, 2, 0))                   # top at ground level
    riverbed = box("Riverbed", (3, -1, -0.5), (4, 1, -0.3))           # entirely below ground
    r = audit({"objects": chair() + [floor, riverbed]})
    assert r["sunk_parts"] == []


def test_floating_gap_is_reported():
    r = audit({"objects": chair(backrest_z=0.7)})
    assert r["floating_gaps"] == [0.23]


def test_separate_objects_on_the_ground_are_not_floating():
    bench = [box("Bench", (0, 0, 0), (1.5, 0.5, 0.8))]
    bin_ = [box("Bin", (2.0, 0, 0), (2.4, 0.4, 0.7))]     # half a metre away, standing on the ground
    r = audit({"objects": bench + bin_})
    assert r["components"] == 2 and not r["defect"]


def test_wall_mounted_model_is_elevated_not_floating():
    cabinet = [box("Body", (0, 0, 1.2), (0.8, 0.3, 1.9)), box("Door", (0, -0.02, 1.2), (0.8, 0, 1.9))]
    r = audit({"objects": cabinet})
    assert r["elevated"] and not r["defect"]


def test_default_names_and_parents_are_counted():
    objs = [box("Cube", (0, 0, 0), (1, 1, 1)), box("Cube.001", (0, 0, 1), (1, 1, 2), parent="Cube"),
            box("Seat", (0, 0, 2), (1, 1, 2.1))]
    r = audit({"objects": objs})
    assert r["default_named"] == 2 and r["parented"] == 1


def test_no_meshes_is_reported_not_a_defect():
    r = audit({"objects": [{"name": "Camera", "type": "CAMERA", "bbox": None}]})
    assert r["meshes"] == 0 and not r["defect"]


def test_components_are_largest_first():
    groups = components([box("a", (5, 5, 0), (6, 6, 1)), box("b", (0, 0, 0), (1, 1, 1)),
                         box("c", (1, 0, 0), (2, 1, 1))], gap=0.01)
    assert [len(g) for g in groups] == [2, 1]
