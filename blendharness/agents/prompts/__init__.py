"""Prompt files: every system prompt and task template lives here as a plain .md file.

Kept out of the code so prompts can be read, diffed and reviewed on their own. Each file is used
verbatim (some are templates with {placeholders} filled by str.format).

    coder.md                 Coder system prompt
    critic.md                Critic system prompt (open question: what is wrong?)
    skill_review.md          critic.md made lenient for reusable components (skill admission)
    verifier.md              Verifier system prompt (closed question: was each issue fixed?)
    librarian.md             Librarian: distil skills from an approved script
    librarian_revise.md      Librarian repair round: fix rejected skills using the gate's reason
    refine_edit.md           revision task with edit tools; the current script is pinned by the context policy
    refine_edit_no_pin.md    same, but carries the script itself (ablation: edit tools without compaction)
    refine_full_script.md    revision task for full-script rewrites (M3 baseline)
"""

from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
