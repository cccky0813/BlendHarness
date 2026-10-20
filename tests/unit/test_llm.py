"""LLMClient without the network: the OpenAI client is replaced by a fake that yields stream chunks.

Covers what went wrong in real runs: tool calls arriving in fragments, streams cut off silently,
transient errors that must be retried, and config errors that must not be.
"""

from types import SimpleNamespace as NS

import openai
import pytest

from blendharness.core.llm import client as llm_module
from blendharness.core.llm import IncompleteStreamError, LLMClient


def chunk(content=None, tool_calls=None, finish=None, usage=None):
    delta = NS(content=content, tool_calls=tool_calls, model_extra={})
    return NS(id="req-1", usage=usage, choices=[NS(delta=delta, finish_reason=finish)])


def tc_piece(index, id=None, name=None, args=""):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


USAGE = NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=None, completion_tokens_details=None)


@pytest.fixture
def client(monkeypatch, tracer):
    # placeholder endpoint/key: the real OpenAI client is swapped out below, nothing is sent anywhere
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:9")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)   # no real backoff in tests
    c = LLMClient("fake-model", tracer, max_retries=3)
    c.prices = {"input": 1.0, "output": 5.0}
    return c


def answer_with(client, *attempts):
    """Each attempt is a list of chunks to stream, or an exception to raise."""
    queue = list(attempts)

    def create(**kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return iter(item)
    client.client = NS(chat=NS(completions=NS(create=create)))
    return queue


def test_tool_call_fragments_are_reassembled(client):
    answer_with(client, [
        chunk(tool_calls=[tc_piece(0, id="call_a", name="execute_bpy", args='{"co')]),
        chunk(tool_calls=[tc_piece(0, args='de": "x=1"}'), tc_piece(1, id="call_b", name="inspect_scene", args="{}")]),
        chunk(finish="tool_calls"),
        NS(id="req-1", usage=USAGE, choices=[]),          # include_usage: last chunk has no choices
    ])
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert [(c.id, c.name, c.arguments) for c in resp.tool_calls] == [
        ("call_a", "execute_bpy", {"code": "x=1"}), ("call_b", "inspect_scene", {})]
    assert resp.raw_message["tool_calls"][0]["function"]["arguments"] == '{"code": "x=1"}'
    assert (resp.input_tokens, resp.output_tokens) == (100, 20)
    assert resp.cost_usd == pytest.approx((100 * 1.0 + 20 * 5.0) / 1e6)


def test_invalid_json_arguments_are_kept_for_the_loop(client):
    answer_with(client, [chunk(tool_calls=[tc_piece(0, id="c", name="t", args="{bad")]), chunk(finish="tool_calls")])
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp.tool_calls[0].arguments == {"__invalid_json__": "{bad"}


def test_stream_without_finish_reason_is_retried(client, tracer):
    answer_with(client,
                [chunk(content='{"skills')],                           # silently cut off
                [chunk(content="hello"), chunk(finish="stop")])
    resp = client.chat([{"role": "user", "content": "hi"}])
    assert resp.text == "hello"
    span = tracer.spans[-1]
    assert span.attrs["attempts"] == 2 and "IncompleteStreamError" in span.attrs["retry_1"]


def test_transient_errors_give_up_after_max_retries(client, tracer):
    cut = [chunk(content="x")]
    answer_with(client, cut, cut, cut)
    with pytest.raises(IncompleteStreamError):
        client.chat([{"role": "user", "content": "hi"}])
    assert tracer.spans[-1].status == "error" and tracer.spans[-1].attrs["attempts"] == 3


def test_config_errors_are_not_retried(client):
    # how a relay reports "no channel for this model": a 503 whose body says model_not_found
    not_configured = openai.InternalServerError.__new__(openai.InternalServerError)
    Exception.__init__(not_configured, "Error code: 503 - model_not_found")
    remaining = answer_with(client, not_configured, [chunk(content="never"), chunk(finish="stop")])
    with pytest.raises(openai.InternalServerError):
        client.chat([{"role": "user", "content": "hi"}])
    assert len(remaining) == 1                                  # the second attempt never happened
