"""Offline replay of code retrieval on recorded sessions (docs/multiturn_design.md 5.7): recall@k of BM25, vectors
and hybrid (RRF), on the turns the sessions actually ran. Costs nothing: no model call, no Blender.

For every turn k >= 2 of every session: the script before the turn (checkpoint t{k-1}_done), the request (plus the
objects the ledger keeper resolved, when the session had structured memory), and the gold = the sections of the
before-script that the Coder's edit really touched (touched_sections, line-based). Any memory strategy's sessions
work; M2 sessions are the intended source, so M3's retriever is judged on edits it did not influence.

    python eval/replay_retrieval.py runs/sessions/*                 # default k = 1 3 5
    python eval/replay_retrieval.py runs/sessions/* --k 1 3 --out runs/analysis/retrieval_replay.json
"""

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blendharness.core.session import Checkpointer  # noqa: E402
from blendharness.memory.session.retrieval import MODES, LocalEmbedder, Retriever, recall_at_k  # noqa: E402
from blendharness.memory.session.sections import split_sections, touched_sections  # noqa: E402


def turns_of(session_dir: Path) -> list[dict]:
    """One case per editing turn: before-script, query, gold sections."""
    cp = Checkpointer(session_dir)
    summary = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    cases = []
    for t in summary["turns"]:
        k = t["turn"]
        before, after = cp.load(f"t{k - 1:02d}_done") if k > 1 else None, cp.load(f"t{k:02d}_done")
        fork = cp.load("fork")                     # a fork's first turn starts from the branch point
        if before is None and fork is not None and len(fork["turns"]) == k - 1:
            before = fork
        if not before or not after or not before.get("current_script"):
            continue
        b, a = before["current_script"], after["current_script"]
        refs = [o for r in (t.get("ledger") or {}).get("references", []) for o in r["objects"]]
        cases.append({"session": session_dir.name, "turn": k, "request": t["request"],
                      "query": " ".join([t["request"], *refs]), "before": b,
                      "gold": touched_sections(b, a), "n_sections": len(split_sections(b))})
    return cases


def replay(cases: list[dict], ks: list[int], retriever: Retriever) -> dict:
    rows = []
    for c in cases:
        if not c["gold"]:
            continue                      # the turn changed nothing in the script: nothing to find
        secs = split_sections(c["before"])
        row = {k: c[k] for k in ("session", "turn", "request", "gold", "n_sections")}
        for mode in MODES:
            ranked = [n for n, _ in retriever.rank(c["query"], secs, mode)]
            row[mode] = {"top": ranked[:max(ks)], **{f"recall@{k}": recall_at_k(ranked, c["gold"], k) for k in ks}}
        rows.append(row)
    table = {mode: {f"recall@{k}": round(statistics.mean(r[mode][f"recall@{k}"] for r in rows), 3) if rows else None
                    for k in ks} for mode in MODES}
    return {"n_turns": len(rows), "skipped_no_change": len(cases) - len(rows), "mean": table, "turns": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--k", nargs="+", type=int, default=[1, 3, 5])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--cache", type=Path, default=Path("runs/analysis/retrieval_vectors.db"),
                    help="SQLite vector cache, reused across replays")
    args = ap.parse_args()
    cases = [c for d in args.sessions if (d / "session.json").exists() for c in turns_of(d)]
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    result = replay(cases, args.k, Retriever(LocalEmbedder(), sqlite3.connect(args.cache)))
    print(f"{result['n_turns']} editing turns ({result['skipped_no_change']} without script changes skipped)")
    print(f"{'mode':8s} " + " ".join(f"recall@{k:<3d}" for k in args.k))
    for mode, row in result["mean"].items():
        print(f"{mode:8s} " + " ".join(f"{row[f'recall@{k}'] if row[f'recall@{k}'] is not None else '-':<9}" for k in args.k))
    for r in result["turns"]:
        print(f"  {r['session']} t{r['turn']}: gold={r['gold']} hybrid top={r['hybrid']['top'][:3]}")
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
