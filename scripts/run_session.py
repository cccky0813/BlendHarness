"""Interactive multi-turn session: type one request per turn, see where the renders went, keep going.

    python scripts/run_session.py                          # new session in runs/<timestamp>_session
    python scripts/run_session.py --resume runs/<dir>      # continue a session after its last finished turn
    python scripts/run_session.py --fork runs/<dir> 3      # branch off after turn 3 of another session

An empty line or Ctrl-D ends the session (it can be resumed later).
"""

import argparse
import time
from pathlib import Path

from blendharness.workflow.build import BuildConfig
from blendharness.workflow.session import Session, SessionConfig

parser = argparse.ArgumentParser()
parser.add_argument("--resume", metavar="DIR")
parser.add_argument("--fork", nargs=2, metavar=("DIR", "TURN"))
parser.add_argument("--coder-model", default=BuildConfig.coder_model)
parser.add_argument("--rounds", type=int, default=BuildConfig.rounds)
parser.add_argument("--memory", default="history", choices=["history", "structured", "retrieval"])
args = parser.parse_args()

cfg = SessionConfig(BuildConfig(coder_model=args.coder_model, rounds=args.rounds), memory=args.memory)
session_dir = Path(args.resume) if args.resume else Path("runs") / time.strftime("%Y%m%d_%H%M%S_session")
session = Session(session_dir, cfg, resume=bool(args.resume),
                  fork_from=(args.fork[0], int(args.fork[1])) if args.fork else None)
print(f"session: {session_dir} ({len(session.state.turns)} turn(s) so far). Empty line to stop.")
while True:
    try:
        request = input(f"\nturn {len(session.state.turns) + 1}> ").strip()
    except EOFError:
        break
    if not request:
        break
    rec = session.turn(request)
    renders = sorted((Path(rec["run_dir"]) / "renders").glob("*/*.png"))
    print(f"--> {'approved' if rec['approved'] else rec['status']} after {rec['n_rounds']} round(s), "
          f"${rec['cost_usd']:.3f}; declared: {rec['declared_changes'][-1] if rec['declared_changes'] else '-'}")
    if renders:
        print(f"    latest renders: {renders[-1].parent}")
print(f"\nsaved: {session_dir / 'session.json'}")
