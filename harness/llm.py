"""LLM client: one place that talks to the model API.

Every call opens an "llm" span, so tokens, cost, latency, retries and errors are traced
automatically. Agents never call the API directly; they go through LLMClient.chat().
"""

import base64
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
    reasoning_tokens: int = 0     # part of output_tokens spent on hidden "thinking"
    cached_tokens: int = 0
    cost_usd: Optional[float] = None
    raw_message: dict[str, Any] = field(default_factory=dict)  # to append back into messages


@dataclass
class _Raw:
    """One model reply in a provider-neutral shape, whether it arrived streamed or in one piece."""
    id: str = ""
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, str]] = field(default_factory=list)  # {"id", "name", "arguments"(JSON str)}
    finish_reason: str = ""
    usage: Any = None

    @classmethod
    def from_completion(cls, completion) -> "_Raw":
        msg = completion.choices[0].message
        return cls(
            id=completion.id or "",
            text=msg.content or "",
            reasoning=getattr(msg, "reasoning_content", None) or (msg.model_extra or {}).get("reasoning_content") or "",
            tool_calls=[{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or ""}
                        for tc in msg.tool_calls or []],
            finish_reason=completion.choices[0].finish_reason or "",
            usage=completion.usage,
        )

    def assistant_message(self) -> dict[str, Any]:
        """The assistant turn to append to the history (the API needs its tool_calls back)."""
        m: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.reasoning:
            m["reasoning_content"] = self.reasoning
        if self.tool_calls:
            m["tool_calls"] = [{"id": tc["id"], "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                               for tc in self.tool_calls]
        return m


def image_message(text: str, image_paths: list[str], role: str = "user") -> dict[str, Any]:
    """A multimodal chat message: text followed by images (sent inline as base64 data URLs)."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in image_paths:
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"},
                        "_path": str(p)})  # private marker so payload logs can show the file instead
    return {"role": role, "content": content}


def _strip_private(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove our private `_path` markers before sending to the API."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            m = {**m, "content": [{k: v for k, v in part.items() if k != "_path"} for part in m["content"]]}
        out.append(m)
    return out


def _redact_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For payload logs: replace huge base64 images with their file path."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            parts = []
            for part in m["content"]:
                if part.get("type") == "image_url":
                    parts.append({"type": "image_url", "image": part.get("_path", "<inline image>")})
                else:
                    parts.append(part)
            m = {**m, "content": parts}
        out.append(m)
    return out


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
        max_tokens: int = 32000,
        max_retries: int = 6,  # transient errors only: ~1 min of backoff survives a network blip
        timeout_s: float = 300,
        extra_body: Optional[dict[str, Any]] = None,  # provider-specific, e.g. {"reasoning_effort": "low"}
        stream: bool = True,
    ) -> None:
        self.stream = stream
        self.model = model
        self.tracer = tracer
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.extra_body = extra_body or {}
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
        with self.tracer.span("llm", self.model, agent=agent, n_messages=len(messages),
                              extra_body=self.extra_body or None) as span:
            completion = self._create_with_retry(messages, tools, span)
            resp = self._parse(completion)
            span.set(
                request_id=completion.id or None,  # provider's id: quote it in support tickets
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                reasoning_tokens=resp.reasoning_tokens,
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
            messages=_strip_private(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.stream:
                    completion = self._stream(kwargs)
                else:
                    completion = _Raw.from_completion(self.client.chat.completions.create(**kwargs))
                span.set(attempts=attempt)
                return completion
            except RETRYABLE as exc:
                # Some relays report config errors (e.g. unknown model) as 503; retrying won't help.
                if attempt == self.max_retries or _is_config_error(exc):
                    span.set(attempts=attempt)
                    raise
                wait = min(2 ** attempt, 32)  # exponential backoff: 2, 4, 8, 16, 32 s (~62 s in total)
                span.set(**{f"retry_{attempt}": f"{type(exc).__name__}, waited {wait}s"})
                time.sleep(wait)
            # Any other error (bad request, auth, unknown model) is our bug: fail fast, no retry.

    def _stream(self, kwargs: dict[str, Any]) -> "_Raw":
        """Receive the answer chunk by chunk. Data keeps flowing during long generations, so
        proxies with a read timeout (e.g. a relay's 120 s limit -> HTTP 524) don't cut us off."""
        stream = self.client.chat.completions.create(**kwargs, stream=True,
                                                     stream_options={"include_usage": True})
        raw = _Raw()
        text, reasoning = [], []
        calls: dict[int, dict[str, str]] = {}  # tool calls arrive in pieces, keyed by their index
        for chunk in stream:
            raw.id = raw.id or chunk.id
            if chunk.usage:  # with include_usage, the last chunk carries the token counts
                raw.usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                text.append(delta.content)
            rc = getattr(delta, "reasoning_content", None) or (delta.model_extra or {}).get("reasoning_content")
            if rc:
                reasoning.append(rc)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                slot["id"] = slot["id"] or (tc.id or "")
                if tc.function:
                    slot["name"] = slot["name"] or (tc.function.name or "")
                    slot["arguments"] += tc.function.arguments or ""  # JSON string, streamed in fragments
            if choice.finish_reason:
                raw.finish_reason = choice.finish_reason
        raw.text, raw.reasoning = "".join(text), "".join(reasoning)
        raw.tool_calls = [calls[i] for i in sorted(calls)]
        return raw

    def _parse(self, completion: "_Raw") -> LLMResponse:
        tool_calls = []
        for tc in completion.tool_calls:
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                # The model produced invalid JSON; keep the raw string so the loop can report it.
                args = {"__invalid_json__": tc["arguments"]}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

        usage = completion.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        out_details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = (getattr(out_details, "reasoning_tokens", 0) or 0) if out_details else 0

        return LLMResponse(
            text=completion.text,
            tool_calls=tool_calls,
            finish_reason=completion.finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_tokens=cached_tokens,
            cost_usd=self._cost(input_tokens, output_tokens, cached_tokens),
            raw_message=completion.assistant_message(),
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
            json.dumps({"messages": _redact_images(messages), "tools": tools, "response": resp.raw_message},
                       ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
