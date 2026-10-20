"""LLM client: one place that talks to the model API.

Every call opens an "llm" span, so tokens, cost, latency, retries and errors are traced
automatically. Agents never call the API directly; they go through LLMClient.chat().
"""

import json
import time
from typing import Any, Optional

import openai
from openai import OpenAI

from blendharness.core.llm.messages import LLMResponse, ToolCall, RawReply, redact_images, strip_private
from blendharness.core.llm.providers import load_prices, provider_env
from blendharness.core.tracing import Tracer


class IncompleteStreamError(Exception):
    """The stream ended without a finish_reason: the connection was cut mid-answer, silently."""


# Errors worth retrying: the request itself was fine, the network/server was not.
RETRYABLE = (
    IncompleteStreamError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)

CONFIG_ERROR_MARKERS = ("model_not_found",)


def _is_config_error(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in CONFIG_ERROR_MARKERS)


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
            messages=strip_private(messages),
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
                    completion = RawReply.from_completion(self.client.chat.completions.create(**kwargs))
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

    def _stream(self, kwargs: dict[str, Any]) -> "RawReply":
        """Receive the answer chunk by chunk. Data keeps flowing during long generations, so
        proxies with a read timeout (e.g. a relay's 120 s limit -> HTTP 524) don't cut us off."""
        stream = self.client.chat.completions.create(**kwargs, stream=True,
                                                     stream_options={"include_usage": True})
        raw = RawReply()
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
        if not raw.finish_reason:
            # Seen in the wild: 2 tokens ('{"skills') then the stream just ended, no error raised.
            raise IncompleteStreamError(f"stream ended after {len(raw.text)} chars without finish_reason")
        return raw

    def _parse(self, completion: "RawReply") -> LLMResponse:
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
            json.dumps({"messages": redact_images(messages), "tools": tools, "response": resp.raw_message},
                       ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
