# BlendHarness as an MCP server

The sandboxed Blender tools that BlendHarness's own Coder uses are also available to any MCP client
(Claude Desktop, Claude Code, Cursor, ...). The client's model becomes the modeler; the server needs
**no API key**, only Blender.

```bash
pip install -e ".[mcp]"          # adds the official MCP Python SDK (mcp 2.x)
blendharness-mcp --help
```

## Tools

| Tool | Permission | MCP annotation | What it does |
|---|---|---|---|
| `execute_bpy` | execute | destructive | Run a complete bpy script in a fresh, empty scene |
| `edit_script` | execute | destructive | Apply exact replacements to the current script, all-or-nothing, then run it |
| `view_script` | read | read-only | Show the current script with line numbers |
| `inspect_scene` | read | read-only | List objects: location, size, vertices, materials |
| `load_skill` | write | not destructive | Make a verified function from the skill library available in every script |
| `render_views` | read | read-only | Render the model (orbit views + top view) and return the images |

The server runs the same `ToolRegistry` as the agent loop: scripts pass the static sandbox guard
(no file system, process or network access), errors come back as readable messages (`isError`),
and every call is a span in `runs/mcp_<time>/trace.jsonl`. The learned skills are listed in the
server instructions.

## Client configuration

Use absolute paths: clients start the server from their own working directory.

**Claude Code**

```bash
claude mcp add blendharness -- /path/to/env/bin/blendharness-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS)

```json
{
  "mcpServers": {
    "blendharness": {
      "command": "/path/to/env/bin/blendharness-mcp",
      "env": { "BLENDER_PATH": "/Applications/Blender.app/Contents/MacOS/Blender" }
    }
  }
}
```

Options: `--skills-dir <dir>` (default: the repository's `skills/`, `''` for none) and
`--run-dir <dir>` (default: `runs/mcp_<time>/` in the repository).
