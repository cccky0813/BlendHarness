"""Model providers and prices: which endpoint and key a model name maps to, and what tokens cost.

Everything is OpenAI-chat-completions compatible, so one client class covers all providers;
only the base URL and the API key differ (read from .env at the repository root).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
# Explicit path: works no matter which directory the program is started from.
load_dotenv(ROOT / ".env")  # reads <PROVIDER>_BASE_URL / <PROVIDER>_API_KEY into os.environ

PRICES_PATH = ROOT / "config" / "prices.json"

# Model-name prefix -> env var prefix. All providers speak the OpenAI chat-completions
# protocol, so one client class covers them; only the URL and key differ.
PROVIDERS = [
    ("deepseek", "DEEPSEEK"),
    ("gpt", "JUDGE"),     # the offline judge (eval/judge.py): its own key; falls back to the relay if unset
    ("gemini", "JUDGE"),
    ("", "OPENAI"),       # fallback: the relay (Claude, GPT, Gemini...)
]


def provider_env(model: str) -> tuple[str, str]:
    """The first provider whose model prefix matches AND whose URL and key are set in the environment."""
    for model_prefix, env_prefix in PROVIDERS:
        url, key = os.environ.get(f"{env_prefix}_BASE_URL"), os.environ.get(f"{env_prefix}_API_KEY")
        if model.startswith(model_prefix) and url and key:
            return url, key
    raise ValueError(f"No provider configured for model {model!r} (set <PROVIDER>_BASE_URL and _API_KEY in .env)")


def load_prices() -> dict[str, dict[str, float]]:
    if PRICES_PATH.exists():
        return json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    return {}
