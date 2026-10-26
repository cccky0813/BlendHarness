"""Ledger keeper: turns the user's new request into ledger updates and resolves references to existing objects.

One cheap call (no tools). Its proposal is only applied after deterministic checks (check_proposal): it may only
supersede active requirements, must give a reason, must add at least one requirement, and may only point at objects
that exist. One retry with the problems listed; after that the request is added to the ledger verbatim, so a bad
answer can lose a nuance but never a requirement.
"""

import json
import re
from dataclasses import dataclass, field

from blendharness.agents.prompts import load_prompt
from blendharness.core.llm import LLMClient

LEDGER_PROMPT = load_prompt("ledger")
MAX_TEXT = 300


@dataclass
class LedgerUpdate:
    add: list[str] = field(default_factory=list)
    supersede: list[dict] = field(default_factory=list)       # {"id", "by": int | None, "reason"}
    references: list[dict] = field(default_factory=list)      # {"phrase", "objects": [names]}
    problems: list[str] = field(default_factory=list)         # why the proposal was (partly) rejected
    fallback: bool = False                                     # True: request added verbatim, nothing superseded
    attempts: int = 0


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def check_proposal(data: dict | None, active_ids: set[str], object_names: set[str]) -> tuple[LedgerUpdate, list[str]]:
    """The usable part of a proposal, plus the problems that make it unusable (empty list = accept)."""
    if data is None:
        return LedgerUpdate(), ["the reply was not a JSON object"]
    problems = []
    add = [str(a).strip() for a in data.get("add") or [] if str(a).strip()]
    if not add:
        problems.append("'add' is empty: every request states at least one requirement")
    if any(len(a) > MAX_TEXT for a in add):
        problems.append(f"an 'add' item is longer than {MAX_TEXT} characters")
    supersede = []
    for s in data.get("supersede") or []:
        sid, by, reason = s.get("id"), s.get("by"), str(s.get("reason") or "").strip()
        if sid not in active_ids:
            problems.append(f"supersede id {sid!r} is not an active requirement")
        elif not reason:
            problems.append(f"supersede {sid}: no reason")
        elif by is not None and not (isinstance(by, int) and 0 <= by < len(add)):
            problems.append(f"supersede {sid}: 'by' must index an 'add' item or be null")
        else:
            supersede.append({"id": sid, "by": by, "reason": reason})
    references = []
    for r in data.get("references") or []:      # unknown names are dropped, not fatal: the Coder can inspect_scene
        names = [n for n in r.get("objects") or [] if n in object_names]
        if r.get("phrase") and names:
            references.append({"phrase": str(r["phrase"]), "objects": names})
    return LedgerUpdate(add, supersede, references), problems


def update_ledger(llm: LLMClient, ledger_text: str, registry_text: str, episodes_text: str, request: str,
                  active_ids: set[str], object_names: set[str]) -> LedgerUpdate:
    with llm.tracer.span("agent", "ledger_keeper") as span:
        user = (f"Active requirements:\n{ledger_text or '(none yet: this is the first turn)'}\n\n"
                f"Scene registry:\n{registry_text or '(empty scene)'}\n\nRecent turns:\n{episodes_text or '(none)'}\n\n"
                f"NEW request:\n{request}")
        messages = [{"role": "system", "content": LEDGER_PROMPT}, {"role": "user", "content": user}]
        for attempt in (1, 2):
            resp = llm.chat(messages, agent="ledger_keeper")
            result, problems = check_proposal(parse(resp.text), active_ids, object_names)
            if not problems:
                result.attempts = attempt
                span.set(attempts=attempt, n_add=len(result.add), n_supersede=len(result.supersede),
                         n_references=len(result.references))
                return result
            messages += [{"role": "assistant", "content": resp.text},
                         {"role": "user", "content": "Fix these problems and reply with the JSON only:\n- "
                                                     + "\n- ".join(problems)}]
        span.set(attempts=2, fallback=True, problems=problems)
        return LedgerUpdate(add=[request], problems=problems, fallback=True, attempts=2)
