"""Deterministic views of session memory: building the scene registry from a snapshot, one-line episodes, and the
compact texts the Coder and Critic read. No model writes any of these, so they cannot invent objects."""

import re
from typing import Any, Optional

_FIRST = re.compile(r"^[A-Za-z]+")


def group_of(name: str) -> str:
    """'Cottage_Roof' -> 'Cottage', 'WellBucket' -> 'Well', 'Tree2_Fol0' -> 'Tree'."""
    head = re.split(r"[_.\-\s]", name, maxsplit=1)[0]
    head = re.sub(r"(?<=[a-z])(?=[A-Z]).*", "", head)      # CamelCase: keep the first word
    m = _FIRST.match(head)
    return m.group(0).capitalize() if m else name


def _box(o: dict) -> Optional[dict]:
    b = o.get("bbox")
    return {"min": b["min"], "max": b["max"]} if b else None


def registry_rows(snapshot: dict, previous: list[dict], turn: int, declared: list[dict],
                  requirement_of_turn: Optional[str] = None) -> list[dict[str, Any]]:
    """The registry after `turn`: every mesh object, its group, box and colors; when it was added and last changed
    (carried over from `previous`, updated when its box or colors differ); the requirement it was added for."""
    prev = {r["name"]: r for r in previous}
    declared_added = {n for d in declared for n in d.get("added", [])}
    rows = []
    for o in snapshot.get("objects", []):
        if o.get("type") != "MESH":
            continue
        box, colors = _box(o), o.get("material_colors") or []
        old = prev.get(o["name"])
        if old is None:
            row = {"added_turn": turn, "changed_turn": turn,
                   "requirement": requirement_of_turn if (o["name"] in declared_added or turn == 1) else None}
        else:
            changed = old["bbox"] != box or old["colors"] != colors
            row = {"added_turn": old["added_turn"], "changed_turn": turn if changed else old["changed_turn"],
                   "requirement": old["requirement"]}
        rows.append({"name": o["name"], "grp": group_of(o["name"]), "bbox": box, "colors": colors, **row})
    return rows


def render_registry(rows: list[dict], max_groups: int = 40) -> str:
    """Compact scene registry: one line per group of objects (the Coder can inspect_scene for full detail)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["grp"], []).append(r)
    lines = []
    for g, members in list(groups.items())[:max_groups]:
        boxes = [m["bbox"] for m in members if m["bbox"]]
        where = ""
        if boxes:
            lo = [min(b["min"][i] for b in boxes) for i in range(3)]
            hi = [max(b["max"][i] for b in boxes) for i in range(3)]
            where = f" center=[{(lo[0] + hi[0]) / 2:.2f}, {(lo[1] + hi[1]) / 2:.2f}] z={lo[2]:.2f}..{hi[2]:.2f}"
        names = ", ".join(m["name"] for m in members[:8]) + (f" (+{len(members) - 8} more)" if len(members) > 8 else "")
        added = min(m["added_turn"] for m in members)
        changed = max(m["changed_turn"] for m in members)
        lines.append(f"- {g}: {names}{where}; added turn {added}" + (f", last changed turn {changed}" if changed > added else ""))
    if len(groups) > max_groups:
        lines.append(f"- ... {len(groups) - max_groups} more groups (use inspect_scene)")
    return "\n".join(lines)


def render_ledger(reqs: list[dict]) -> str:
    """Active requirements, oldest first, with the turn each came from."""
    return "\n".join(f"[{r['id']}, turn {r['turn']}] {r['text']}" for r in reqs if r["status"] == "active")


def episode_line(turn: int, request: str, declared: list[dict], approved: bool, regressions: int) -> str:
    added = sorted({n for d in declared for n in d.get("added", [])})
    modified = sorted({n for d in declared for n in d.get("modified", [])})
    removed = sorted({n for d in declared for n in d.get("removed", [])})
    parts = [f"added {', '.join(added)}" if added else "", f"modified {', '.join(modified)}" if modified else "",
             f"removed {', '.join(removed)}" if removed else ""]
    what = "; ".join(p for p in parts if p) or ("built the scene" if turn == 1 else "no declared change")
    return (f"Turn {turn}: \"{request[:90]}\" -> {what}. Review: {'approved' if approved else 'not approved'}"
            + (f"; {regressions} unrequested change(s)" if regressions else "") + ".")


def render_episodes(rows: list[dict], last: int = 8) -> str:
    return "\n".join(r["summary"] for r in rows[-last:])
