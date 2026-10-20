"""Message-shaped data: what a model reply looks like to the rest of the harness, plus chat-message helpers."""

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


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
class RawReply:
    """One model reply in a provider-neutral shape, whether it arrived streamed or in one piece."""
    id: str = ""
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, str]] = field(default_factory=list)  # {"id", "name", "arguments"(JSON str)}
    finish_reason: str = ""
    usage: Any = None

    @classmethod
    def from_completion(cls, completion) -> "RawReply":
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


def strip_private(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove our private `_path` markers before sending to the API."""
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            m = {**m, "content": [{k: v for k, v in part.items() if k != "_path"} for part in m["content"]]}
        out.append(m)
    return out


def redact_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
