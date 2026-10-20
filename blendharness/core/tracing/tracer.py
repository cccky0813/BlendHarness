"""Span-based tracing: every LLM call and tool call becomes one JSON line in trace.jsonl.

Spans nest automatically: a span opened inside another span records it as its parent,
so one agent run forms a tree (run -> agent step -> llm call / tool call).
"""

import json
import time
import traceback
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# The span currently "open" in this thread/task; new spans use it as their parent.
_current_span: ContextVar[Optional["Span"]] = ContextVar("current_span", default=None)


@dataclass
class Span:
    kind: str                      # "run" | "agent" | "llm" | "tool"
    name: str                      # e.g. agent name, model name, tool name
    run_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    latency_s: float = 0.0
    status: str = "ok"             # "ok" | "error"
    error: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)  # free-form: tokens, cost, args, output...

    def set(self, **kwargs: Any) -> None:
        self.attrs.update(kwargs)


class Tracer:
    def __init__(self, run_dir: str | Path, run_id: Optional[str] = None, resume: bool = False) -> None:
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"
        self.spans: list[Span] = []
        if resume and self.path.exists():  # a resumed run: earlier spans still count in summary()
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    self.spans.append(Span(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue           # a line cut off by the interruption

    @contextmanager
    def span(self, kind: str, name: str, **attrs: Any):
        parent = _current_span.get()
        span = Span(
            kind=kind,
            name=name,
            run_id=self.run_id,
            parent_id=parent.span_id if parent else None,
            attrs=dict(attrs),
        )
        token = _current_span.set(span)
        t0 = time.perf_counter()
        try:
            yield span
        except Exception as exc:
            span.status = "error"
            span.error = f"{type(exc).__name__}: {exc}"
            span.set(traceback=traceback.format_exc(limit=5))
            raise
        finally:
            span.latency_s = round(time.perf_counter() - t0, 3)
            _current_span.reset(token)
            self._write(span)

    def _write(self, span: Span) -> None:
        self.spans.append(span)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(span), ensure_ascii=False, default=str) + "\n")

    def summary(self) -> dict[str, Any]:
        """Aggregate LLM usage per agent: calls, tokens, cost, latency."""
        per_agent: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                     "cost_usd": 0.0, "latency_s": 0.0, "errors": 0}
        )
        for s in self.spans:
            if s.kind != "llm":
                continue
            row = per_agent[s.attrs.get("agent", "unknown")]
            row["calls"] += 1
            row["input_tokens"] += s.attrs.get("input_tokens", 0) or 0
            row["output_tokens"] += s.attrs.get("output_tokens", 0) or 0
            row["cost_usd"] += s.attrs.get("cost_usd", 0.0) or 0.0
            row["latency_s"] += s.latency_s
            row["errors"] += s.status == "error"
        total = {k: sum(r[k] for r in per_agent.values())
                 for k in ("calls", "input_tokens", "output_tokens", "cost_usd", "latency_s", "errors")}
        return {"per_agent": dict(per_agent), "total": total}
