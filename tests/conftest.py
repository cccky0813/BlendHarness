"""Shared pytest fixtures (the test doubles themselves live in tests/fakes.py)."""

from pathlib import Path

import pytest

from harness.tracing import Tracer
from tests.fakes import FakeSession, ScriptedLLM


@pytest.fixture
def tracer(tmp_path: Path) -> Tracer:
    return Tracer(tmp_path / "run")


@pytest.fixture
def scripted(tracer):
    """Usage: llm = scripted([reply(...), reply(...)])"""
    return lambda replies: ScriptedLLM(replies, tracer)


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()
