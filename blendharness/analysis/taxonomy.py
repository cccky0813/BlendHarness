"""Failure taxonomy: what kind of problem did the Critic find? Classified by ROOT CAUSE.

    spatial      floating        not supported: hovers, gap, detached from what should hold it
                 interpenetration parts overlap / intersect, or sink into something (also when that hides them)
                 placement       wrong position, orientation or alignment relative to other parts
                 proportion      wrong size relative to other parts or to the real object
    non-spatial  missing         a requested part does not exist at all
                 material        color, material, texture, glow
                 shape           wrong form of a part (too simple, wrong primitive, wrong profile)
                 other

"The path is invisible" whose cause is "it sits below the ground" is interpenetration, not missing:
keyword matching gets this wrong, so issues are labelled by an LLM (one cheap batched call), cached on disk
so the same issue text is never paid for twice. keyword_category() remains as an offline fallback.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

SPATIAL = ("floating", "interpenetration", "placement", "proportion")
NON_SPATIAL = ("missing", "material", "shape", "other")
CATEGORIES = SPATIAL + NON_SPATIAL

PROMPT = """Classify each problem found in a 3D model by its ROOT CAUSE. Categories:
- floating: a part is not supported (hovers, gap, detached from what should hold it)
- interpenetration: parts overlap/intersect, or a part sinks into another or below the ground (even if that makes it invisible)
- placement: wrong position, orientation or alignment relative to other parts (not floating, not overlapping)
- proportion: wrong size relative to other parts or to the real object
- missing: a requested part does not exist at all (not merely hidden)
- material: color, material, texture or glow
- shape: wrong form of a part (too simple, wrong primitive, wrong profile)
- other: anything else

Reply with ONLY a JSON object mapping each id to one category, e.g. {"1": "floating", "2": "material"}.

Problems:
"""

_KEYWORDS = [  # fallback, first match wins
    ("floating", r"float|detach|hover|mid-air|gap|not touching|not attached|not connected|unsupported"),
    ("interpenetration", r"intersect|overlap|buried|embedded|sunk|sink|below the ground|inside|penetrat|z-fight"),
    ("proportion", r"proportion|oversiz|undersiz|too (large|big|small|thin|thick|wide|tall|short|long)|scale"),
    ("placement", r"misalign|align|orient|rotat|position|placed|line up|facing|wrong side"),
    ("missing", r"missing|absent|does not exist|no \w+ at all"),
    ("material", r"material|colou?r|texture|emissi|glow"),
    ("shape", r"shape|primitive|placeholder|profile|blocky|too simple"),
]


def keyword_category(text: str) -> str:
    t = text.lower()
    return next((name for name, pattern in _KEYWORDS if re.search(pattern, t)), "other")


def _key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


class IssueClassifier:
    """classify(texts) -> categories, using a cache file and an LLM for unseen texts.

    `ask` is any function prompt -> reply text (e.g. lambda p: llm.chat([...]).text); None = keywords only.
    """

    def __init__(self, cache_path: str | Path, ask: Optional[Callable[[str], str]] = None, batch: int = 40) -> None:
        self.cache_path = Path(cache_path)
        self.cache: dict[str, str] = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.ask = ask
        self.batch = batch

    def classify(self, texts: list[str]) -> list[str]:
        """Only LLM labels are cached: a keyword fallback must not block a later, better label."""
        unseen = list(dict.fromkeys(t for t in texts if _key(t) not in self.cache))
        if self.ask:
            for start in range(0, len(unseen), self.batch):
                chunk = unseen[start:start + self.batch]
                labels = self._ask_llm(chunk)
                for i, text in enumerate(chunk, 1):
                    if labels.get(str(i)) in CATEGORIES:
                        self.cache[_key(text)] = labels[str(i)]
            if unseen:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        return [self.cache.get(_key(t)) or keyword_category(t) for t in texts]

    def _ask_llm(self, chunk: list[str]) -> dict[str, str]:
        listing = "\n".join(f"{i}. {t[:400]}" for i, t in enumerate(chunk, 1))
        reply = self.ask(PROMPT + listing)
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        try:
            return json.loads(match.group(0)) if match else {}
        except json.JSONDecodeError:
            return {}                  # fall back to keywords for this chunk
