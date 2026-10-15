"""LLM client: one place that talks to the model API.

Every call opens an "llm" span, so tokens, cost, latency, retries and errors are traced
automatically. Agents never call the API directly; they go through LLMClient.chat().
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import openai
from dotenv import load_dotenv
from openai import OpenAI

from harness.tracing import Tracer

ROOT = Path(__file__).resolve().parents[1]
# Explicit path: works no matter which directory the program is started from.
load_dotenv(ROOT / ".env")  # reads <PROVIDER>_BASE_URL / <PROVIDER>_API_KEY into os.environ

PRICES_PATH = ROOT / "config" / "prices.json"

# Model-name prefix -> env var prefix. All providers speak the OpenAI chat-completions
# protocol, so one client class covers them; only the URL and key differ.
PROVIDERS = [
    ("deepseek", "DEEPSEEK"),
    ("", "OPENAI"),  # fallback: the relay (Claude, GPT, Gemini...)
]


def provider_env(model: str) -> tuple[str, str]:
    for model_prefix, env_prefix in PROVIDERS:
        if model.startswith(model_prefix):
            return os.environ[f"{env_prefix}_BASE_URL"], os.environ[f"{env_prefix}_API_KEY"]
    raise ValueError(f"No provider configured for model {model!r}")

# Errors worth retrying: the request itself was fine, the network/server was not.
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]   # already parsed from the JSON string the model returned


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: Optional[float] = None
    raw_message: dict[str, Any] = field(default_factory=dict)  # to append back into messages


CONFIG_ERROR_MARKERS = ("model_not_found",)


def _is_config_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in CONFIG_ERROR_MARKERS)


def load_prices() -> dict[str, dict[str, float]]:
    if PRICES_PATH.exists():
        return json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    return {}


class LLMClient:
    def __init__(
        self,
        model: str,
        tracer: Tracer,
        temperature: float = 0.2,
        max_tokens: int = 8000,
        max_retries: int = 3,
        timeout_s: float = 180,
    ) -> None:
        self.model = model
        self.tracer = tracer
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        base_url, api_key = provider_env(model)
        # max_retries=0: we retry ourselves so every attempt is visible in the trace.
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,
        )
        self.prices = load_prices().get(model)  # {"input": $/M tokens, "output": ..., "cached_input": ...}

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        agent: str = "unknown",
    ) -> LLMResponse:
        with self.tracer.span("llm", self.model, agent=agent, n_messages=len(messages)) as span:
            completion = self._create_with_retry(messages, tools, span)
            resp = self._parse(completion)
            span.set(
                request_id=getattr(completion, "id", None),  # provider's id: quote it in support tickets
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cached_tokens=resp.cached_tokens,
                cost_usd=resp.cost_usd,
                finish_reason=resp.finish_reason,
                tool_calls=[{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                output_preview=resp.text[:500],
            )
            self._save_payload(span.span_id, messages, tools, resp)
            return resp

    def _create_with_retry(self, messages, tools, span):
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
        for attempt in range(1, self.max_retries + 1):
            try:
                completion = self.client.chat.completions.create(**kwargs)
                span.set(attempts=attempt)
                return completion
            except RETRYABLE as exc:
                # Some relays report config errors (e.g. unknown model) as 503; retrying won't help.
                if attempt == self.max_retries or _is_config_error(exc):
                    span.set(attempts=attempt)
                    raise
                wait = 2 ** attempt  # exponential backoff: 2s, 4s, 8s...
                span.set(**{f"retry_{attempt}": f"{type(exc).__name__}, waited {wait}s"})
                time.sleep(wait)
            # Any other error (bad request, auth, unknown model) is our bug: fail fast, no retry.

    def _parse(self, completion) -> LLMResponse:
        choice = completion.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                # The model produced invalid JSON; keep the raw string so the loop can report it.
                args = {"__invalid_json__": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        usage = completion.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0

        return LLMResponse(
            text=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=self._cost(input_tokens, output_tokens, cached_tokens),
            raw_message=msg.model_dump(exclude_none=True),
        )

    def _cost(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> Optional[float]:
        if not self.prices:
            return None  # unknown price: report None rather than a misleading 0
        p = self.prices
        uncached = input_tokens - cached_tokens
        cost = (
            uncached * p["input"]
            + cached_tokens * p.get("cached_input", p["input"])
            + output_tokens * p["output"]
        ) / 1_000_000
        return round(cost, 6)

    def _save_payload(self, span_id, messages, tools, resp: LLMResponse) -> None:
        """Full request/response goes to a side file; the trace line stays small."""
        d = self.tracer.run_dir / "payloads"
        d.mkdir(exist_ok=True)
        (d / f"{span_id}.json").write_text(
            json.dumps({"messages": messages, "tools": tools, "response": resp.raw_message},
                       ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
