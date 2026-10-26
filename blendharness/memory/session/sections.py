"""Split a scene script into named sections, for the outline (L4) and code retrieval (M3).

Coders rarely write one function per part. They write helper functions, then a flat list of top-level calls grouped
under banner comments (`# ---- stone well ----`). So a section is either
    a top-level def / class     (kind "helper", named by the function), or
    a run of top-level code     (kind "block"), starting at a comment that follows a blank line, named by that
                                 comment (or, without one, by the objects it creates).
Every section records the object names its string literals mention, which links the scene registry to the code.

Ground truth for retrieval: `touched_sections(before, after)` = the sections of the script BEFORE a turn whose lines
the turn changed (deleted, replaced, or inserted after). It is computed on lines, so renaming a banner still counts.
"""

import ast
import difflib
import re
from dataclasses import dataclass, field

from blendharness.memory.session.views import group_of

_NAME_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\- ]{0,60}$")
_BANNER = re.compile(r"^#[\s#=\-*~]*(.*?)[\s#=\-*~]*$")


@dataclass
class Section:
    name: str
    kind: str              # "helper" | "block"
    start: int             # 1-based, inclusive
    end: int
    text: str
    comment: str = ""
    objects: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        """What retrieval indexes: name, comment, object names, then the code."""
        return f"{self.name}\n{self.comment}\n{' '.join(self.objects)}\n{self.text}"


def _objects(code: str) -> list[str]:
    """Names the code gives objects and materials: the first string argument of every call, or a name= keyword
    ('Well_Roof'; f"Window_Frame_{i+1}" -> 'Window_Frame'). In order, deduplicated."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return list(dict.fromkeys(m.group(1) for m in re.finditer(r"""["']([A-Z][A-Za-z0-9_.]*)["']""", code)))
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = node.args[:1] + [k.value for k in node.keywords if k.arg == "name"]
        for arg in args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                names.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                names.append("".join(v.value for v in arg.values if isinstance(v, ast.Constant)).rstrip("_.- "))
    names = [n for n in names if _NAME_LIKE.match(n)]
    return list(dict.fromkeys(sorted(names, key=lambda n: code.find(n))))


def split_sections(script: str) -> list[Section]:
    lines = script.splitlines()
    if not lines:
        return []
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return [Section("script", "block", 1, len(lines), script, objects=_objects(script))]
    # top-level statements with their full line span (decorators included)
    spans = [((n.decorator_list[0].lineno if getattr(n, "decorator_list", None) else n.lineno), n.end_lineno, n)
             for n in tree.body]
    sections: list[Section] = []
    block: list[tuple[int, int, ast.stmt]] = []

    def flush():
        nonlocal block
        if block:
            start, end = block[0][0], block[-1][1]
            start = _comment_start(lines, start)
            text = "\n".join(lines[start - 1:end])
            comment = _comment_text(lines, start)
            objs = _objects(text)
            imports = all(isinstance(n, (ast.Import, ast.ImportFrom)) for _, _, n in block)
            sections.append(Section(comment or ("imports" if imports else _dominant_group(objs) or "block"), "block",
                                    start, end, text, comment, objs))
        block = []

    for start, end, node in spans:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            flush()
            s = _comment_start(lines, start)
            text = "\n".join(lines[s - 1:end])
            sections.append(Section(node.name, "helper", s, end, text, _comment_text(lines, s), _objects(text)))
            continue
        if block and _starts_new_block(lines, block[-1][1], start):
            flush()
        block.append((start, end, node))
    flush()
    _dedupe(sections)
    return sections


def _comment_start(lines: list[str], start: int) -> int:
    """Move a section's start up over the comment lines directly above it."""
    while start > 1 and lines[start - 2].lstrip().startswith("#") and not lines[start - 2].startswith("#!"):
        start -= 1
    return start


def _comment_text(lines: list[str], start: int) -> str:
    line = lines[start - 1].strip()
    if not line.startswith("#"):
        return ""
    return _BANNER.match(line).group(1).strip()


def _starts_new_block(lines: list[str], prev_end: int, start: int) -> bool:
    """A blank line followed by a comment, between the previous statement and this one."""
    gap = lines[prev_end:start - 1]
    return any(not a.strip() and b.lstrip().startswith("#") for a, b in zip(gap, gap[1:]))


def _dominant_group(objects: list[str]) -> str:
    groups = [group_of(o) for o in objects]
    return max(set(groups), key=groups.count) if groups else ""


def _dedupe(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for s in sections:
        n = seen.get(s.name, 0)
        seen[s.name] = n + 1
        if n:
            s.name = f"{s.name} ({n + 1})"


def find_section(sections: list[Section], name: str) -> Section | None:
    """Exact name, else case-insensitive, else the unique section whose name contains `name`."""
    for s in sections:
        if s.name == name:
            return s
    low = name.strip().lower()
    for s in sections:
        if s.name.lower() == low:
            return s
    hits = [s for s in sections if low and low in s.name.lower()]
    return hits[0] if len(hits) == 1 else None


def render_outline(sections: list[Section], max_objects: int = 8) -> str:
    """One line per section: name, kind, line range, objects (the Coder loads code with view_section)."""
    out = []
    for s in sections:
        objs = ", ".join(s.objects[:max_objects]) + (f" (+{len(s.objects) - max_objects})" if len(s.objects) > max_objects else "")
        out.append(f"- [{s.kind}] {s.name}  (lines {s.start}-{s.end})" + (f": {objs}" if objs else ""))
    return "\n".join(out)


def touched_sections(before: str, after: str) -> list[str]:
    """Names of the sections of `before` that the change to `after` touched (see module docstring)."""
    sections = split_sections(before)
    if not sections:
        return []
    a, b = before.splitlines(), after.splitlines()
    touched: set[str] = set()

    def owner(line: int) -> str | None:              # 1-based line of `before`
        for s in sections:
            if s.start <= line <= s.end:
                return s.name
        prev = [s for s in sections if s.end < line]  # blank / comment gap: the section above
        return prev[-1].name if prev else sections[0].name

    for tag, i1, i2, _, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:                                    # replaced or deleted lines
            touched.update(filter(None, (owner(i) for i in range(i1 + 1, i2 + 1))))
        else:                                          # pure insertion after line i1
            touched.add(owner(max(i1, 1)))
    return [s.name for s in sections if s.name in touched]
