"""M3: splitting scripts into sections, the ground truth of which sections an edit touched, BM25 / vector / RRF
retrieval with the SQLite vector cache, the outline view, and a retrieval session on fakes."""

import hashlib
import json
import sqlite3

from blendharness.agents.coder import script_pin
from blendharness.memory.session.retrieval import BM25, Retriever, recall_at_k, tokenize
from blendharness.memory.session.sections import find_section, render_outline, split_sections, touched_sections
from blendharness.workflow.build import BuildConfig
from blendharness.workflow.session import SessionConfig, run_session
from tests.fakes import reply
from tests.unit.test_build import CRITIC_OK, Models, coder_builds
from tests.unit.test_session import ADD_BACK, T1, T2, sessions  # noqa: F401 (fixture)
from tests.unit.test_session_memory import ledger

VILLAGE = '''import bpy

def box(name, x0, x1, mat):
    return make(name, x0, x1, mat)

# ---- materials ----
M_ROOF = mat("RoofTiles", (0.4, 0.2, 0.1))
M_STONE = mat("Stone", (0.5, 0.5, 0.5))

# ---- cottage ----
box("Cottage_Walls", 0, 1, M_STONE)
box("Cottage_Roof", 0, 1, M_ROOF)
# the door
box("Cottage_Door", 0, 1, M_STONE)

# ---- stone well ----
for i in range(2):
    box(f"Well_Post_{i+1}", 0, 1, M_STONE)
box("Well_Roof", 0, 1, M_ROOF)

box("Ground", 0, 1, M_STONE)
'''


class HashEmbedder:
    """Deterministic stand-in: a hashed bag of tokens, so shared words mean similar vectors."""
    model = "hash-64"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in tokenize(t):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out


def test_sections_follow_helpers_and_banner_comments():
    secs = split_sections(VILLAGE)
    assert [(s.name, s.kind) for s in secs] == [("imports", "block"), ("box", "helper"), ("materials", "block"),
                                                ("cottage", "block"), ("stone well", "block")]
    cottage, well = secs[3], secs[4]
    assert cottage.objects == ["Cottage_Walls", "Cottage_Roof", "Cottage_Door"]   # an inline comment does not split
    assert "Well_Post" in well.objects and "Ground" in well.objects                # no banner: stays in the block
    assert find_section(secs, "Stone Well") is well and find_section(secs, "well") is well
    assert "[helper] box" in render_outline(secs)
    assert split_sections("def broken(:\n")[0].name == "script"


def test_touched_sections_is_line_based():
    recolor = VILLAGE.replace('mat("RoofTiles", (0.4, 0.2, 0.1))', 'mat("RoofTiles", (0.8, 0.1, 0.1))')
    assert touched_sections(VILLAGE, recolor) == ["materials"]
    bucket = VILLAGE.replace('box("Well_Roof", 0, 1, M_ROOF)\n', 'box("Well_Roof", 0, 1, M_ROOF)\nbox("Bucket", 0, 1, M_STONE)\n')
    assert touched_sections(VILLAGE, bucket) == ["stone well"]
    renamed = VILLAGE.replace("# ---- cottage ----", "# ---- the house ----")
    assert touched_sections(VILLAGE, renamed) == ["cottage"]
    assert touched_sections(VILLAGE, VILLAGE) == []


def test_bm25_vector_and_hybrid_ranking():
    secs = split_sections(VILLAGE)
    assert tokenize("WellBucket_2 the posts") == ["well", "bucket", "post"]
    bm = BM25([s.search_text() for s in secs]).scores("well roof")
    assert max(range(len(secs)), key=bm.__getitem__) == 4
    db = sqlite3.connect(":memory:")
    emb = HashEmbedder()
    r = Retriever(emb, db)
    for mode in ("bm25", "vector", "hybrid"):
        assert r.search("make the cottage door bigger", secs, 1, mode) == ["cottage"], mode
    calls = emb.calls
    r.rank("anything", secs, "vector")
    assert emb.calls == calls + 1                    # sections came from the SQLite cache, only the query embedded
    assert r.cache.hits >= len(secs)
    assert recall_at_k(["a", "b", "c"], ["c", "d"], 3) == 0.5 and recall_at_k(["a"], [], 3) is None


def test_outline_pin_shows_focus_sections_and_view_section_loads_others():
    from blendharness.tools.blender_tools import BlenderTools
    b = BlenderTools(session=None, renders_dir="/tmp/unused")
    b.current_script, b.script_view, b.focus = VILLAGE, "outline", ["stone well"]
    pin = script_pin(b)
    assert "- [block] cottage" in pin and 'box("Well_Roof"' in pin and 'box("Cottage_Door"' not in pin
    assert 'box("Cottage_Door"' in b.view_section("cottage").content
    assert b.view_section("nope").is_error
    assert "view_section" in [t.name for t in b.as_tools()]
    b.script_view = "full"
    assert "view_section" not in [t.name for t in b.as_tools()] and 'box("Cottage_Door"' in script_pin(b)


def test_retrieval_session_retrieves_and_scores_its_own_recall(tmp_path, sessions):
    cfg = SessionConfig(BuildConfig(coder_model="coder", critic_model="critic", rounds=1), memory="retrieval",
                        ledger_model="ledger", retrieval_k=1)
    models = Models(coder=coder_builds() + ADD_BACK, critic=[reply(CRITIC_OK)] * 2,
                    ledger=[ledger(["A stool."]), ledger(["The stool has a backrest."])])
    summary = run_session([T1, T2], cfg, tmp_path / "s", log=lambda m: None, llm_factory=models,
                          session_factory=sessions, embedder=HashEmbedder())
    t2 = summary["turns"][1]
    assert t2["retrieval"]["focus"] and t2["touched_sections"]
    assert t2["retrieval"]["recall"] in (0.0, 1.0)
    first_view = models.llms["coder"].seen[2]
    pinned = json.dumps(first_view)
    assert "as an outline" in pinned and "view_section" in json.dumps(models.llms["coder"].seen[2])
    state = json.loads((tmp_path / "s" / "turns" / "t02" / "result.json").read_text())
    assert state["config"]["script_view"] == "outline"
