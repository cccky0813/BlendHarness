"""Checkpointer: atomic JSON saves, ordering, lookup by label; Tracer resume."""

import json

from blendharness.core.session import Checkpointer
from blendharness.core.tracing import Tracer


def test_save_and_load_by_label_and_latest(tmp_path):
    cp = Checkpointer(tmp_path)
    assert cp.load() is None and cp.labels() == []
    cp.save("round0", {"n": 0})
    cp.save("round1", {"n": 1})
    cp.save("round1", {"n": 2})                   # saved again (e.g. after a resume): the latest wins
    assert cp.labels() == ["round0", "round1", "round1"]
    assert cp.load()["n"] == 2 and cp.load("round0")["n"] == 0 and cp.load("round1")["n"] == 2
    assert cp.load("missing") is None


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    cp = Checkpointer(tmp_path)
    path = cp.save("round0", {"script": "x" * 10000})
    assert path.name == "000_round0.json"
    assert list((tmp_path / "checkpoints").glob("*.tmp")) == []
    assert json.loads(path.read_text())["state"]["script"] == "x" * 10000


def test_a_leftover_temp_file_is_ignored(tmp_path):
    cp = Checkpointer(tmp_path)
    cp.save("round0", {"n": 0})
    (tmp_path / "checkpoints" / "001_round1.json.tmp").write_text('{"half writ')   # crash mid-write
    assert cp.labels() == ["round0"] and cp.load()["n"] == 0


def test_resumed_tracer_counts_earlier_spans(tmp_path):
    first = Tracer(tmp_path)
    with first.span("llm", "m", agent="coder", input_tokens=10, cost_usd=0.5):
        pass
    with (tmp_path / "trace.jsonl").open("a") as f:
        f.write('{"kind": "llm", "na')                                          # line cut by a crash
    resumed = Tracer(tmp_path, resume=True)
    with resumed.span("llm", "m", agent="coder", input_tokens=5, cost_usd=0.25):
        pass
    total = resumed.summary()["total"]
    assert (total["calls"], total["input_tokens"], total["cost_usd"]) == (2, 15, 0.75)
    assert Tracer(tmp_path).spans == []                                        # without resume: a fresh count
