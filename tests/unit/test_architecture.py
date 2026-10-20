"""The layering rule, enforced: each layer may only import from itself and the layers below it.

    core  <-  sandbox, memory  <-  tools  <-  agents  <-  workflow

A diagram in the README can silently go out of date; this test cannot.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "blendharness"

ALLOWED = {  # layer -> layers it may import from (besides itself)
    "core": set(),
    "sandbox": {"core"},
    "memory": {"core"},
    "tools": {"core", "sandbox", "memory"},
    "agents": {"core", "sandbox", "memory", "tools"},
    "workflow": {"core", "sandbox", "memory", "tools", "agents"},
}


def blendharness_imports(path: Path) -> set[str]:
    """Top-level layer names this file imports from blendharness (e.g. {"core", "tools"})."""
    layers = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) else \
                [a.name for a in node.names] if isinstance(node, ast.Import) else []
        for name in names:
            parts = name.split(".")
            if parts[0] == "blendharness" and len(parts) > 1:
                layers.add(parts[1])
    return layers


def modules() -> list:
    params = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE)
        if len(rel.parts) > 1:                      # skip blendharness/__init__.py itself
            params.append(pytest.param(path, rel.parts[0], id=str(rel)))
    return params


@pytest.mark.parametrize("path, layer", modules())
def test_imports_follow_the_layering(path, layer):
    forbidden = blendharness_imports(path) - ALLOWED[layer] - {layer}
    assert not forbidden, f"{layer}/ must not import from {sorted(forbidden)}"


def test_every_layer_is_known():
    layers = {p.name for p in PACKAGE.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert layers == set(ALLOWED)


def test_sandbox_server_is_self_contained():
    """server.py runs inside Blender's own Python, where our package is not installed."""
    assert blendharness_imports(PACKAGE / "sandbox" / "server.py") == set()


def test_rails_do_not_depend_on_the_runtime():
    """The runtime consults rails, never the other way round (it would also be a circular import)."""
    for path in (PACKAGE / "core" / "rails").glob("*.py"):
        assert "blendharness.core.runtime" not in path.read_text(encoding="utf-8"), path.name
