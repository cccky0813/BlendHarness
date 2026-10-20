"""Self-evolution from failures: distil lessons from past builds into a lesson library.

    python scripts/reflect.py                                 # placement + proportion -> lessons/
    python scripts/reflect.py --categories placement --dry-run    # show the cases, no LLM call
    python scripts/reflect.py --out lessons_v2

Uses the issue labels cached by eval/analyze.py (run that first). The lessons only become part of a build
with BuildConfig(lessons_dir=...); whether they help is measured on UNSEEN tasks (eval, variant "lessons").
"""

import argparse
import json
import time
from pathlib import Path

from blendharness.agents.reflector import Case
from blendharness.analysis import IssueClassifier, find_runs, load_trajectory
from blendharness.core.llm import LLMClient
from blendharness.core.tracing import Tracer
from blendharness.memory.lessons import LessonLibrary
from blendharness.workflow.build import effort_body
from blendharness.workflow.learning.lessons import learn_lessons, select_cases

ROOT = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser()
parser.add_argument("--categories", nargs="+", default=["placement", "proportion"])
parser.add_argument("--out", default=str(ROOT / "lessons"), help="lesson library folder")
parser.add_argument("--model", default="claude-sonnet-4-6")
parser.add_argument("--exclude", nargs="*", default=["smoke"])
parser.add_argument("--dry-run", action="store_true", help="print the cases, no LLM call")
args = parser.parse_args()

runs = ROOT / "runs"
trajectories = [load_trajectory(d, runs) for d in find_runs(runs)]
trajectories = [t for t in trajectories if t.experiment not in args.exclude]
issues = [i for t in trajectories for i in t.issues]
labels = IssueClassifier(runs / "analysis" / "issue_labels.json").classify([i.text for i in issues])
for issue, label in zip(issues, labels):
    issue.category = label

if args.dry_run:
    for category in args.categories:
        cases: list[Case] = select_cases(trajectories, category)
        print(f"=== {category}: {len(cases)} cases from {len({c.task for c in cases})} tasks")
        for n, c in enumerate(cases, 1):
            print(c.render(n), "\n")
    raise SystemExit

run_dir = runs / "reflect" / time.strftime("%Y%m%d_%H%M%S")
llm = LLMClient(args.model, Tracer(run_dir, run_id=run_dir.name), extra_body=effort_body(args.model, "low"))
library = LessonLibrary(args.out)
report = learn_lessons(llm, trajectories, args.categories, library)
(run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\nadded {len(report['added'])}, rejected {len(report['rejected'])}; cost ${llm.tracer.summary()['total']['cost_usd']:.4f}")
for r in report["rejected"]:
    print(f"  rejected {r['id']} at {r['gate']}: {r['reason']}")
print(f"\nlibrary {library.path}:")
for lesson in library.all():
    print(f"- [{lesson.category}] {lesson.id}: {lesson.text}\n    sources: {lesson.sources}")
