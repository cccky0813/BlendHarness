"""Static check of model-written code BEFORE it runs in Blender (no execution, just the syntax tree).

Modeling scripts need bpy, bmesh, mathutils, math, random... and nothing that touches the file system,
other processes or the network. So the guard rejects:
  - imports of system modules (os, subprocess, socket, ...)
  - calls of builtins that open files or run arbitrary code (open, exec, eval, __import__, ...)
  - dunder attributes used to escape restrictions (obj.__class__.__subclasses__(), f.__globals__, ...)
  - Blender operators that quit Blender or write/read files outside the sandbox's control

This is defense in depth, not a security boundary: a determined attacker can obfuscate around any
static check. The boundary is process isolation (and containers for a public service). What the guard
does well is catch the realistic case early, with a clear message the model can act on.
"""

import ast
from typing import Optional

BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket", "ctypes", "multiprocessing", "threading",
    "signal", "pathlib", "glob", "tempfile", "pickle", "marshal", "importlib", "builtins",
    "urllib", "http", "requests", "ftplib", "smtplib", "webbrowser", "asyncio",
}
BLOCKED_CALLS = {"open", "exec", "eval", "compile", "__import__", "breakpoint", "input", "globals", "vars"}
BLOCKED_ATTRIBUTES = {
    "__class__", "__subclasses__", "__globals__", "__builtins__", "__code__", "__bases__", "__mro__",
    "__dict__", "__getattribute__", "__loader__", "__spec__",
}
# bpy.ops.wm.<name>: quitting or touching .blend files / scripts on disk
BLOCKED_WM_OPERATORS = {"quit_blender", "save_mainfile", "save_as_mainfile", "open_mainfile", "read_homefile",
                        "read_userpref", "save_userpref", "append", "link", "url_open"}

ALLOWED_HINT = ("Modeling scripts may use bpy, bmesh, mathutils, math, random, itertools and similar pure "
                "modules; file system, process and network access are disabled in this sandbox.")


def check_code(code: str) -> Optional[str]:
    """Return a message describing the first violation, or None if the code may run.

    A syntax error is NOT reported here: running the code gives the model Python's own, better message.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        problem = _violation(node)
        if problem:
            line = getattr(node, "lineno", "?")
            return f"blocked by the sandbox policy: {problem} (line {line}). {ALLOWED_HINT}"
    return None


def _violation(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in BLOCKED_MODULES:
                return f"`import {alias.name}` is not allowed"
    elif isinstance(node, ast.ImportFrom):
        if (node.module or "").split(".")[0] in BLOCKED_MODULES:
            return f"`from {node.module} import ...` is not allowed"
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
        if node.func.id == "__import__" and _literal_module(node) not in (None, *BLOCKED_MODULES):
            return None  # inline import of a harmless module, e.g. __import__('mathutils').Vector (seen in real runs)
        return f"calling `{node.func.id}()` is not allowed"
    elif isinstance(node, ast.Attribute):
        if node.attr in BLOCKED_ATTRIBUTES:
            return f"accessing `.{node.attr}` is not allowed"
        if node.attr in BLOCKED_WM_OPERATORS and _dotted(node.value) == "bpy.ops.wm":
            return f"`bpy.ops.wm.{node.attr}` is not allowed"
    elif isinstance(node, ast.Name) and node.id == "__builtins__":
        return "accessing `__builtins__` is not allowed"
    return None


def _literal_module(call: ast.Call) -> Optional[str]:
    """Top-level module name of __import__('a.b'), or None when the argument is not a string literal."""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value.split(".")[0]
    return None


def _dotted(node: ast.AST) -> str:
    """`bpy.ops.wm` for the expression bpy.ops.wm, '' for anything that is not a plain dotted name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""
