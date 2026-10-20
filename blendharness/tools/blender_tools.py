"""Blender tools for agents, built on the sandbox (BlenderSession).

State model: the ONLY state is `current_script`, the last script that ran without error.
Every exec starts from an empty scene, so render/inspect simply re-run current_script first.
No hidden Blender state -> reproducible, and survives a sandbox restart.

The render script and Scene-Inspector are ported from the original LL3M_demo1 project.
"""

import re
import time
from pathlib import Path
from typing import Callable, Optional

from blendharness.core.tools import Tool, ToolResult
from blendharness.sandbox.session import BlenderSession
from blendharness.tools.render import RENDER_PREFIX, RENDER_SCRIPT
from blendharness.tools.scene import SCENE_INSPECTOR_SCRIPT, SCENE_PREFIX, extract_prefixed_json

VIEW_PAGE_LINES = 120


class BlenderTools:
    def __init__(self, session: BlenderSession, renders_dir: str | Path, resolution: int = 512,
                 preamble_provider: Optional[Callable[[], str]] = None) -> None:
        # Generic hook: code to prepend to every script run (e.g. loaded skills). This class does not
        # know what the code is; tools/skill_tools.py provides it (dependency inversion).
        self.preamble_provider = preamble_provider
        self.session = session
        self.renders_dir = Path(renders_dir).resolve()
        self.resolution = resolution
        self.current_script = ""          # last script that executed successfully
        self.history: list[dict] = []     # every attempted script and its outcome

    # ---------- tool functions (what the agent calls) ----------
    def execute_bpy(self, code: str) -> ToolResult:
        r = self.session.execute(self._with_skills(code), reset=True)
        r.traceback = self._map_lines(r.traceback)
        r.error = self._map_lines(r.error)
        self.history.append({"code": code, "ok": r.ok, "error": r.error})
        if not r.ok:
            msg = f"ERROR: {r.error}\n{r.traceback}".strip()
            if r.output.strip():
                msg += f"\n--- stdout before the error ---\n{r.output.strip()[-1500:]}"
            return ToolResult(msg, is_error=True)
        previous = self.current_script
        self.current_script = code
        snap = self.snapshot()
        if previous and "error" not in snap and snap["summary"]["mesh_object_count"] == 0:
            # A probe/test snippet must not wipe the model: keep the last working script.
            self.current_script = previous
            self.history[-1]["ok"] = False
            return ToolResult(
                "ERROR: the script ran but produced an EMPTY scene, so it was NOT saved; the previous working "
                "script is still current. execute_bpy always rebuilds the whole scene from your script. "
                "To read values use inspect_scene or view_script; to change the model use edit_script or send "
                "the complete script." + (f"\n--- stdout ---\n{r.output.strip()[-800:]}" if r.output.strip() else ""),
                is_error=True)
        summary = self._scene_summary_line(snap)
        out = r.output.strip()
        return ToolResult(f"OK: script ran in {r.duration_s}s. {summary}" + (f"\n--- stdout ---\n{out[-1500:]}" if out else ""))

    def edit_script(self, edits: list[dict]) -> ToolResult:
        """Apply several exact replacements to current_script in one call, then run the result once.

        All-or-nothing: every old_string must occur exactly once (checked in order, on the text as edited
        so far); if any edit doesn't match or the edited script fails, NOTHING is applied and
        current_script stays the last known-good version.
        """
        if not self.current_script:
            return ToolResult("ERROR: there is no script yet; create one with execute_bpy first.", True)
        if not isinstance(edits, list) or not edits:
            return ToolResult("ERROR: edits must be a non-empty list of {old_string, new_string}.", True)
        edited = self.current_script
        for i, e in enumerate(edits, 1):
            old, new = e.get("old_string", ""), e.get("new_string", "")
            count = edited.count(old) if old else 0
            if count != 1:
                why = "not found" if count == 0 else f"occurs {count} times (add surrounding lines to make it unique)"
                return ToolResult(f"ERROR: edit {i} of {len(edits)}: old_string {why}. No edits were applied; "
                                  "the current script is unchanged and shown in full in your context.", True)
            edited = edited.replace(old, new, 1)
        before = self.current_script
        result = self.execute_bpy(edited)          # sets current_script only on success
        if result.is_error:
            self.current_script = before
            return ToolResult(f"ERROR: the edited script failed, none of the {len(edits)} edits were applied "
                              "(current script unchanged).\n" + result.content.removeprefix("ERROR: "), True)
        return ToolResult(result.content.replace("OK: script ran",
                                                 f"OK: {len(edits)} edit(s) applied, script ran", 1))

    def view_script(self, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        """Show part of current_script with line numbers (paged, so long scripts are fully readable)."""
        if not self.current_script:
            return ToolResult("ERROR: there is no script yet.", True)
        lines = self.current_script.splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), int(end_line) if end_line else start + VIEW_PAGE_LINES - 1)
        body = "\n".join(f"{i:4d}| {lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n... ({len(lines) - end} more lines: call view_script with start_line={end + 1})" if end < len(lines) else ""
        return ToolResult(f"lines {start}-{end} of {len(lines)}\n{body}{more}")

    def inspect_scene(self) -> ToolResult:
        snap = self.snapshot()
        if "error" in snap:
            return ToolResult(f"ERROR: {snap['error']}", is_error=True)
        lines = [f"{snap['summary']['object_count']} objects, {snap['summary']['total_vertex_count']} vertices"]
        for o in snap["objects"]:
            bbox = o.get("bbox") or {}
            lines.append(
                f"- {o['name']} ({o['type']}) loc={o['location']} size={bbox.get('size')} "
                f"verts={o['vertex_count']} materials={o['material_names']}"
                + (f" parent={o['parent']}" if o["parent"] else "")
            )
        return ToolResult("\n".join(lines))

    # ---------- helpers used by the harness / other agents ----------
    def snapshot(self) -> dict:
        """Structured scene state (Scene-Inspector) of current_script."""
        if not self.current_script:
            return {"error": "no successful script yet; call execute_bpy first"}
        r = self.session.execute(self.full_script() + "\n" + SCENE_INSPECTOR_SCRIPT, reset=True)
        if not r.ok:
            return {"error": r.error}
        return extract_prefixed_json(r.output, SCENE_PREFIX) or {"error": "Scene-Inspector returned nothing"}

    def render(self, tag: str, num_views: int = 3, top_view: bool = True) -> list[str]:
        """Render current_script from `num_views` orbit angles (+ an optional high top view)."""
        if not self.current_script:
            return []
        out_dir = self.renders_dir / f"{tag}_{time.strftime('%H%M%S')}"
        script = RENDER_SCRIPT.format(out_dir=str(out_dir), tag=tag, num_views=num_views,
                                       top_view=top_view, res=self.resolution, prefix=RENDER_PREFIX)
        r = self.session.execute(self.full_script() + "\n" + script, reset=True, timeout_s=300)
        if not r.ok:
            return []
        payload = extract_prefixed_json(r.output, RENDER_PREFIX) or {}
        return payload.get("renders", [])

    # ---------- preamble hook ----------
    def preamble(self) -> str:
        return self.preamble_provider() if self.preamble_provider else ""

    def _with_skills(self, code: str) -> str:
        return self.preamble() + code

    def full_script(self) -> str:
        """Standalone script: preamble (e.g. loaded skills) + current_script (what final_script.py contains)."""
        return self._with_skills(self.current_script)

    def _map_lines(self, text: str) -> str:
        """Tracebacks count lines of preamble + script; translate them back to the model's own script."""
        offset = self.preamble().count("\n")
        if not offset or not text:
            return text

        def fix(m: re.Match) -> str:
            n = int(m.group(1))
            return f'File "<your script>", line {n - offset}' if n > offset else f'File "<skill code>", line {n}'
        return re.sub(r'File "<agent_code>", line (\d+)', fix, text)

    def as_tools(self, include_edit: bool = True) -> list[Tool]:
        tools = [
            Tool(
                name="execute_bpy",
                description=(
                    "Execute a COMPLETE Blender 4.4 Python (bpy) script in a fresh, EMPTY scene. "
                    "Each call starts from scratch, so always send the full script, not a diff. "
                    "Returns OK with a scene summary, or the error and traceback."
                ),
                parameters={"type": "object",
                            "properties": {"code": {"type": "string", "description": "Full bpy script."}},
                            "required": ["code"]},
                fn=self.execute_bpy,
            ),
            Tool(
                name="edit_script",
                description=(
                    "Change the current working script without resending all of it. Give ALL the changes you "
                    "need as a list of exact replacements in ONE call: each old_string must occur exactly once "
                    "(applied in order). Then the edited script runs once in a fresh scene. All-or-nothing: if "
                    "any edit does not match or the script fails, nothing is applied. The current script is "
                    "always shown in full at the end of your context."
                ),
                parameters={"type": "object",
                            "properties": {"edits": {
                                "type": "array",
                                "items": {"type": "object",
                                          "properties": {"old_string": {"type": "string", "description": "Exact text to replace."},
                                                         "new_string": {"type": "string", "description": "Replacement text."}},
                                          "required": ["old_string", "new_string"]}}},
                            "required": ["edits"]},
                fn=self.edit_script,
            ),
            Tool(
                name="view_script",
                description=("Show the current working script (last version that ran OK) with line numbers, "
                             f"{VIEW_PAGE_LINES} lines per call; use start_line/end_line to page through it."),
                parameters={"type": "object",
                            "properties": {"start_line": {"type": "integer", "description": "First line (1-based)."},
                                           "end_line": {"type": "integer", "description": "Last line (inclusive)."}},
                            "required": []},
                fn=self.view_script,
            ),
            Tool(
                name="inspect_scene",
                description=(
                    "List every object produced by the last successful script: name, type, location, "
                    "bounding-box size, vertex count, materials, parent. Use it to check proportions "
                    "and that all requested parts exist."
                ),
                parameters={"type": "object", "properties": {}, "required": []},
                fn=self.inspect_scene,
            ),
        ]
        if not include_edit:  # ablation: full-script rewrites only (M3 baseline behaviour)
            tools = [t for t in tools if t.name not in ("edit_script", "view_script")]
        return tools

    def _scene_summary_line(self, snap: dict) -> str:
        if "error" in snap:
            return ""
        s = snap["summary"]
        names = [o["name"] for o in snap["objects"]][:15]
        return f"Scene now has {s['object_count']} objects ({s['mesh_object_count']} meshes): {names}"
