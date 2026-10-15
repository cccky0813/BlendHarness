"""Re-run skill learning on already-approved builds of an experiment (no rebuilding).

Used when the learning step itself was improved or failed for an unrelated reason (e.g. a stream cut
by the network): the approved final_script.py is still on disk, so only the librarian + gates re-run.
Results are stored next to the run as relearn.json (result.json is left as the original record).

Usage: python scripts/relearn.py --exp skills_lc --task-file eval/tasks_skills.json --tasks desk kitchen game_scene
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.llm import LLMClient  # noqa: E402
from harness.tracing import Tracer  # noqa: E402
from memory.learning import learn_from_build  # noqa: E402
from memory.skill_library import SkillLibrary  # noqa: E402
from pipeline.build import BuildConfig, effort_body  # noqa: E402
from tools.blender_session import BlenderSession  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--exp", required=True)
parser.add_argument("--task-file", default="eval/tasks.json")
parser.add_argument("--tasks", nargs="+", required=True)
parser.add_argument("--variant", default="train")
args = parser.parse_args()

prompts = {t["id"]: t["prompt"] for t in json.loads((ROOT / args.task_file).read_text(encoding="utf-8"))}
exp_dir = ROOT / "runs" / "eval" / args.exp
library = SkillLibrary(exp_dir / "skills")
cfg = BuildConfig()

for task_id in args.tasks:  # sequential, in the given order: each task sees the skills added before it
    run_dir = exp_dir / args.variant / f"{task_id}_r1"
    record = json.loads((run_dir / "result.json").read_text())
    if not record["approved"]:
        print(f"skip {task_id}: build was not approved")
        continue
    tracer = Tracer(run_dir / "relearn", run_id=f"{run_dir.name}_relearn")
    with BlenderSession(run_dir / "relearn" / "blender.log") as session:
        learned = learn_from_build(
            LLMClient(cfg.coder_model, tracer, extra_body=effort_body(cfg.coder_model, cfg.coder_effort)),
            LLMClient(cfg.critic_model, tracer), prompts[task_id], (run_dir / "final_script.py").read_text(),
            library, session, source_task=run_dir.name, renders_dir=run_dir / "relearn" / "skill_checks",
            log=lambda m: print(f"[{task_id}] {m}", flush=True))
    learned["cost_usd"] = tracer.summary()["total"]["cost_usd"]
    (run_dir / "relearn.json").write_text(json.dumps(learned, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{task_id}] added {learned['added']} (after repair: {learned['added_after_repair']}), "
          f"cost ${learned['cost_usd']:.3f}", flush=True)

print("\nlibrary:\n" + library.index_text())
