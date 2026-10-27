"""Code retrieval over script sections (M3): BM25, embeddings, and both fused with Reciprocal Rank Fusion.

Why hybrid: BM25 is exact on identifiers ("Well_Roof" in the request -> the block that creates it), embeddings
catch paraphrases ("the little house" -> the cottage block). RRF fuses ranks, not scores, so the two scales never
have to be calibrated against each other: score(d) = sum over rankers of 1 / (RRF_K + rank(d)).

Vectors are cached in SQLite (`embeddings`, keyed by model + text hash) in the session's memory.db: a section that
did not change between turns is never embedded again, and a fork copies the cache along with the memory. A
session has at most a few hundred sections, so search is a brute-force cosine over a numpy matrix; no vector DB.

Embedders: LocalEmbedder (fastembed, BAAI/bge-small-en-v1.5, 384-d, CPU, 64 MB downloaded once to MODEL_DIR; free and
deterministic, which makes offline replays reproducible). Anything with .model and .embed(list[str]) works.
"""

import hashlib
import math
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Protocol

from blendharness.memory.session.sections import Section

# where fastembed keeps downloaded models: models/fastembed in the repo (gitignored), unless FASTEMBED_CACHE_PATH is set
MODEL_DIR = Path(os.environ.get("FASTEMBED_CACHE_PATH") or Path(__file__).resolve().parents[3] / "models" / "fastembed")

RRF_K = 60
MODES = ("bm25", "vector", "hybrid")
_STOP = set("a an the and or of to in on at by for with from into onto is are be it its this that these those "
            "make put add set give change move use so as near next new all some each one two".split())


def tokenize(text: str) -> list[str]:
    """Identifiers split on _ . and camelCase, lowercased, plurals folded: 'WellBucket_2' -> well, bucket."""
    words = re.findall(r"[A-Za-z]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text))
    out = []
    for w in (w.lower() for w in words):
        if len(w) < 2 or w in _STOP:
            continue
        out.append(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for d, length in zip(self.docs, self.lens):
            s = 0.0
            for t in q:
                f = d.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            out.append(s)
        return out


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model = model
        self._m = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._m is None:
            from fastembed import TextEmbedding      # optional dependency: pip install -e .[retrieval]
            self._m = TextEmbedding(self.model, cache_dir=str(MODEL_DIR))
        return [list(map(float, v)) for v in self._m.embed(texts)]


class VectorCache:
    """model + sha1(text) -> vector, in the session's SQLite file."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS embeddings (model TEXT, hash TEXT, dim INTEGER, vec BLOB, "
                   "PRIMARY KEY (model, hash))")
        self.hits = self.misses = 0

    def vectors(self, embedder: Embedder, texts: list[str]):
        import numpy as np
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        found = {h: np.frombuffer(v, dtype=np.float32) for h, v in self.db.execute(
            f"SELECT hash, vec FROM embeddings WHERE model = ? AND hash IN ({','.join('?' * len(keys))})",
            [embedder.model, *keys])} if keys else {}
        todo = [(h, t) for h, t in dict(zip(keys, texts)).items() if h not in found]
        self.hits, self.misses = self.hits + len(keys) - len(todo), self.misses + len(todo)
        if todo:
            for (h, _), v in zip(todo, embedder.embed([t for _, t in todo])):
                arr = np.asarray(v, dtype=np.float32)
                found[h] = arr
                self.db.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                                (embedder.model, h, len(arr), arr.tobytes()))
            self.db.commit()
        return np.stack([found[h] for h in keys])


def _ranks(scores: list[float]) -> list[int]:
    """Indices sorted best first; ties keep script order (stable)."""
    return sorted(range(len(scores)), key=lambda i: -scores[i])


class Retriever:
    def __init__(self, embedder: Embedder | None = None, db: sqlite3.Connection | None = None) -> None:
        self.embedder = embedder
        self.cache = VectorCache(db if db is not None else sqlite3.connect(":memory:")) if embedder else None

    def rank(self, query: str, sections: list[Section], mode: str = "hybrid") -> list[tuple[str, float]]:
        """All sections, best first, as (name, score). Helpers are ranked too (a request can be about one)."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if not sections:
            return []
        docs = [s.search_text() for s in sections]
        rankings, scores = [], {}
        if mode in ("bm25", "hybrid"):
            bm = BM25(docs).scores(query)
            rankings.append([i for i in _ranks(bm) if bm[i] > 0])      # no shared term: no vote
            scores["bm25"] = bm
        if mode in ("vector", "hybrid"):
            if self.embedder is None:
                raise ValueError("vector retrieval needs an embedder")
            import numpy as np
            m = self.cache.vectors(self.embedder, docs)
            q = np.asarray(self.embedder.embed([query])[0], dtype=np.float32)
            cos = (m @ q) / (np.linalg.norm(m, axis=1) * np.linalg.norm(q) + 1e-9)
            rankings.append(_ranks(cos.tolist()))
            scores["vector"] = cos.tolist()
        if mode != "hybrid":
            only = scores[mode]
            return [(sections[i].name, only[i]) for i in _ranks(only)]
        fused = [0.0] * len(sections)
        for r in rankings:
            for rank, i in enumerate(r):
                fused[i] += 1.0 / (RRF_K + rank + 1)
        return [(sections[i].name, fused[i]) for i in _ranks(fused)]

    def search(self, query: str, sections: list[Section], k: int = 3, mode: str = "hybrid") -> list[str]:
        return [name for name, _ in self.rank(query, sections, mode)[:k]]


def recall_at_k(ranked: list[str], gold: list[str], k: int) -> float | None:
    """Share of the gold sections in the top k; None when there is no gold (nothing to find)."""
    if not gold:
        return None
    return len(set(ranked[:k]) & set(gold)) / len(gold)
