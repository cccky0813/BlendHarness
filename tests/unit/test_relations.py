"""Relation checks: repeated parts, mirror pairs, support and near-miss joints, on hand-made snapshots."""

from blendharness.analysis.relations import check_relations, mirror_pairs, numbered_groups
from tests.unit.test_structure import box


def checks(objs: list[dict]) -> list[str]:
    return [f["check"] for f in check_relations({"objects": objs})["findings"]]


def slats(xs: list[float], width: float = 0.04) -> list[dict]:
    return [box(f"Slat_{i}", (x - width / 2, 0, 0), (x + width / 2, 0.02, 0.5)) for i, x in enumerate(xs, 1)]


def test_groups_and_pairs_come_from_names():
    objs = slats([0, 0.1, 0.2]) + [box("Rail_L", (0, 0, 0), (1, 1, 1)), box("Rail_R", (2, 0, 0), (3, 1, 1)),
                                   box("Post_Front_Left", (0, 0, 0), (1, 1, 1)), box("Seat", (0, 0, 0), (1, 1, 1))]
    assert list(numbered_groups(objs)) == ["Slat"]
    assert [(a["name"], b["name"]) for a, b in mirror_pairs(objs)] == [("Rail_L", "Rail_R")]


def test_evenly_spaced_equal_slats_are_fine():
    assert checks(slats([0, 0.1, 0.2, 0.3])) == []


def test_bunched_slats_are_uneven():
    assert checks(slats([0, 0.05, 0.1, 0.3])) == ["uneven_spacing"]


def test_corner_legs_are_not_a_row():
    legs = [box(f"Leg_{i}", (x, y, 0), (x + 0.04, y + 0.04, 0.4)) for i, (x, y) in
            enumerate([(0, 0), (0.5, 0), (0, 0.5), (0.5, 0.5)], 1)]
    assert "uneven_spacing" not in checks(legs)


def test_a_wider_middle_slat_is_a_size_mismatch():
    objs = slats([0, 0.1, 0.2])
    objs[1] = box("Slat_2", (0.07, 0, 0), (0.13, 0.02, 0.5))       # 6 cm instead of 4 cm
    assert checks(objs) == ["size_mismatch"]


def test_mirror_pair_offset_and_size():
    good = [box("Arm_L", (0, 0, 0.5), (0.05, 0.4, 0.55)), box("Arm_R", (1, 0, 0.5), (1.05, 0.4, 0.55))]
    assert checks(good) == []
    lower = [good[0], box("Arm_R", (1, 0, 0.3), (1.05, 0.4, 0.35))]   # 20 cm lower than its partner
    assert checks(lower) == ["pair_misaligned"]
    longer = [good[0], box("Arm_R", (1, 0, 0.5), (1.05, 0.6, 0.55))]
    assert "pair_size_mismatch" in checks(longer)


def test_varied_natural_parts_are_not_compared():
    rocks = [box(f"Rock_{i}", (i, 0, 0), (i + w, d, h)) for i, (w, d, h) in
             enumerate([(0.3, 0.2, 0.1), (0.5, 0.35, 0.2), (0.2, 0.25, 0.15)], 1)]
    assert checks(rocks) == []
    pair = [box("Rock_L", (0, 0, 0), (0.3, 0.2, 0.1)), box("Rock_R", (3, 2, 0), (3.5, 2.3, 0.2))]
    assert checks(pair) == []


def test_grid_tiles_with_joints_are_not_near_misses():
    tiles = [box(f"Paver_{r}_{c}", (c * 0.52, r * 0.52, -0.05), (c * 0.52 + 0.5, r * 0.52 + 0.5, 0))
             for r in range(2) for c in range(2)]
    assert checks(tiles) == []


def test_bracket_on_a_wall_plate_edge_is_not_off_support():
    plate = box("Plate", (0, 0, 1.0), (0.1, 0.02, 1.2))
    arm = box("Arm", (0.02, 0, 1.2), (0.08, 0.25, 1.22))               # cantilevered from the plate's top edge
    assert "off_support" not in checks([plate, arm])


def test_part_hanging_off_its_support():
    table = box("Top", (0, 0, 0.7), (1, 0.6, 0.75))
    vase = box("Vase", (0.95, 0.2, 0.75), (1.15, 0.4, 1.0))            # centre at x = 1.05, past the edge
    assert "off_support" in checks([table, vase])
    vase_ok = box("Vase", (0.4, 0.2, 0.75), (0.6, 0.4, 1.0))
    assert "off_support" not in checks([table, vase_ok])


def test_top_on_four_legs_is_supported():
    legs = [box(f"Leg_{i}", (x, y, 0), (x + 0.05, y + 0.05, 0.7)) for i, (x, y) in
            enumerate([(0, 0), (0.95, 0), (0, 0.55), (0.95, 0.55)], 1)]
    top = box("Top", (0, 0, 0.7), (1, 0.6, 0.75))                      # centre over no single leg
    assert "off_support" not in checks(legs + [top])


def test_joint_that_stops_short_is_a_near_miss():
    post = box("Post", (0, 0, 0), (0.05, 0.05, 1))
    rail = box("Rail", (0.09, 0, 0.9), (1, 0.05, 0.95))                 # 4 cm short of the post
    assert checks([post, rail]) == ["near_miss", "near_miss"]
    touching = box("Rail", (0.05, 0, 0.9), (1, 0.05, 0.95))
    assert checks([post, touching]) == []
