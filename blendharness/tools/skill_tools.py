"""Skill tools: let an agent use the skill library, independent of any execution environment.

Responsibilities (and nothing else):
  - index_text(): the skill index for the agent's system prompt (progressive disclosure, level 1)
  - load_skill(name): a tool returning signature + usage notes (level 2) and marking the skill loaded
  - preamble(): the code of loaded skills, handed to an executor through a generic hook

The executor (BlenderTools) never imports or knows about skills: it only asks a `preamble_provider`
for code to prepend. Dependency inversion keeps both sides reusable (another agent, another executor).
"""

from blendharness.core.tools import Tool, ToolResult
from blendharness.memory.skills import SkillLibrary


class SkillTools:
    def __init__(self, library: SkillLibrary) -> None:
        self.library = library
        self.loaded: list[str] = []          # order of loading = order in the preamble

    def index_text(self) -> str:
        return self.library.index_text()

    def load_skill(self, name: str) -> ToolResult:
        skill = self.library.get(name)
        if skill is None:
            return ToolResult(f"ERROR: no skill named {name!r}. Available: {', '.join(self.library.names()) or 'none'}", True)
        if name not in self.loaded:
            self.loaded.append(name)
        return ToolResult(
            f"OK: skill '{name}' loaded. {skill.function_name} is now defined at the top of EVERY script you run: "
            f"call it, do not copy its code.\n\n{skill.signature()}\n\n{skill.notes}")

    def preamble(self) -> str:
        """Code of all loaded skills (the executor prepends it to every script it runs)."""
        if not self.loaded:
            return ""
        parts = [f"# ---- skill: {n} ----\n" + self.library.get(n).code.strip() for n in self.loaded]
        return "\n\n".join(parts) + "\n# ---- end of skills ----\n"

    def as_tools(self) -> list[Tool]:
        if not self.library.names():
            return []                        # empty library: offer nothing, agent behaves as before
        return [Tool(
            name="load_skill",
            description=("Load a verified reusable modeling function from the skill library (see the list of "
                         "skills in your instructions). Returns its signature and usage notes; afterwards the "
                         "function is available in every script you run."),
            parameters={"type": "object", "properties": {"name": {"type": "string", "description": "Skill name."}},
                        "required": ["name"]},
            fn=self.load_skill,
            permission="write",            # changes what every later script runs with
        )]
