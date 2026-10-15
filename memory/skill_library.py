"""Skill library: long-term memory of verified, reusable modeling functions (M4).

On disk, one folder per skill (Agent-Skills style, human-readable and git-diffable):

    skills/<name>/SKILL.md     front matter (name, description, tags, source_task) + usage notes
    skills/<name>/skill.py     ONE self-contained function `build_<name>(...)` (own imports)
    skills/<name>/stats.json   uses / successes, written by the program (kept apart from SKILL.md)

Progressive disclosure: the agent's context only holds the index (name + one-line description);
`load_skill(name)` reveals the usage notes and signature, and the harness prepends the code when
scripts run, so the model calls the function instead of copying it.
"""

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


@dataclass
class Skill:
    name: str
    description: str
    code: str
    notes: str = ""                       # SKILL.md body: parameters, usage example, caveats
    tags: list[str] = field(default_factory=list)
    source_task: str = ""
    uses: int = 0
    successes: int = 0

    @property
    def function_name(self) -> str:
        return f"build_{self.name}"

    def signature(self) -> str:
        """`def build_x(a, b=1.0):` line plus the docstring, read from the code (no execution)."""
        for node in ast.parse(self.code).body:
            if isinstance(node, ast.FunctionDef) and node.name == self.function_name:
                args = ast.unparse(node.args)
                doc = ast.get_docstring(node) or ""
                return f"def {node.name}({args})" + (f'\n    """{doc}"""' if doc else "")
        return f"def {self.function_name}(...)"


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Minimal `---\\nkey: value\\n---\\nbody` parser (values are plain strings or [a, b] lists)."""
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    _, header, body = text.split("---", 2)
    for line in header.strip().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                meta[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            else:
                meta[key.strip()] = value
    return meta, body.strip()


def validate_skill_code(name: str, code: str) -> Optional[str]:
    """Static checks before a skill is admitted; returns an error message or None."""
    if not NAME_RE.match(name):
        return f"invalid skill name {name!r} (lowercase, digits, underscores)"
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc}"
    functions = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    if f"build_{name}" not in functions:
        return f"code must define a function build_{name}(...)"
    # No top-level side effects: the code is prepended to EVERY script, so only imports, function
    # definitions and docstrings may appear at module level (a stray call would add objects each run).
    for node in tree.body:
        is_docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        if not (isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)) or is_docstring):
            return f"top-level statement not allowed in a skill: {type(node).__name__} (line {node.lineno})"
    return None


class SkillLibrary:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- read ----------
    def names(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "skill.py").exists())

    def get(self, name: str) -> Optional[Skill]:
        d = self.root / name
        if not (d / "skill.py").exists():
            return None
        meta, body = _parse_front_matter((d / "SKILL.md").read_text(encoding="utf-8")) if (d / "SKILL.md").exists() else ({}, "")
        stats = json.loads((d / "stats.json").read_text()) if (d / "stats.json").exists() else {}
        return Skill(name=name, description=meta.get("description", ""), code=(d / "skill.py").read_text(encoding="utf-8"),
                     notes=body, tags=meta.get("tags", []), source_task=meta.get("source_task", ""),
                     uses=stats.get("uses", 0), successes=stats.get("successes", 0))

    def all(self) -> list[Skill]:
        return [s for s in (self.get(n) for n in self.names()) if s]

    def index_text(self) -> str:
        """The part that always sits in the agent's context: one line per skill."""
        skills = self.all()
        if not skills:
            return ""
        lines = [f"- {s.name}: {s.description}" + (f" [used {s.uses}x, {s.successes} ok]" if s.uses else "")
                 for s in skills]
        return "\n".join(lines)

    # ---------- write ----------
    def add(self, skill: Skill, overwrite: bool = False) -> Optional[str]:
        """Store a skill after static validation. Returns an error message or None."""
        error = validate_skill_code(skill.name, skill.code)
        if error:
            return error
        d = self.root / skill.name
        if d.exists() and not overwrite:
            return f"skill {skill.name!r} already exists"
        d.mkdir(parents=True, exist_ok=True)
        header = (f"---\nname: {skill.name}\ndescription: {skill.description}\n"
                  f"tags: [{', '.join(skill.tags)}]\nsource_task: {skill.source_task}\n---\n\n")
        (d / "SKILL.md").write_text(header + skill.notes.strip() + "\n", encoding="utf-8")
        (d / "skill.py").write_text(skill.code.strip() + "\n", encoding="utf-8")
        if not (d / "stats.json").exists():
            (d / "stats.json").write_text(json.dumps({"uses": 0, "successes": 0}), encoding="utf-8")
        return None

    def record_use(self, name: str, success: bool) -> None:
        """Usage statistics: a quality signal for ranking and pruning skills later."""
        path = self.root / name / "stats.json"
        if not path.parent.exists():
            return
        stats = json.loads(path.read_text()) if path.exists() else {"uses": 0, "successes": 0}
        stats["uses"] += 1
        stats["successes"] += int(success)
        path.write_text(json.dumps(stats), encoding="utf-8")
