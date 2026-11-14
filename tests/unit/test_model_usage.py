"""Reading what a model request used out of the provider's answer, in each API's shape."""

from __future__ import annotations

import json

import pytest

from cloud_agent.domain import model_usage
from cloud_agent.domain.model_usage import (
    CHAT,
    MESSAGES,
    RESPONSES,
    TokenUsage,
    UsageReader,
    billable_output,
)

_EVENTS = "text/event-stream; charset=utf-8"
_JSON = "application/json"


def _sse(*events: dict | str, ending: str = "\n\n") -> bytes:
    """An event stream of ``events``, each a ``data:`` line."""
    lines = [
        f"data: {event if isinstance(event, str) else json.dumps(event)}{ending}"
        for event in events
    ]
    return "".join(lines).encode()


def _read(api: str, body: bytes, *, content_type: str, step: int | None = None) -> UsageReader:
    reader = UsageReader(api, content_type=content_type)
    if step is None:
        reader.feed(body)
    else:
        for start in range(0, len(body), step):
            reader.feed(body[start : start + step])
    return reader


# -- Chat Completions ----------------------------------------------------------

_CHAT_STREAM = _sse(
    {"choices": [{"delta": {"content": "Hel"}}], "usage": None},
    {"choices": [{"delta": {"content": 'lo, "usage" and all'}}]},
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "prompt_tokens_details": {"cached_tokens": 1000},
            "completion_tokens_details": {"reasoning_tokens": 120},
        },
    },
    "[DONE]",
)


@pytest.mark.parametrize("step", [None, 1, 7, 64])
def test_a_chat_stream_names_its_usage_on_the_last_chunk(step) -> None:
    reader = _read(CHAT, _CHAT_STREAM, content_type=_EVENTS, step=step)

    # Reasoning is part of the completion count here: the total says so.
    assert reader.usage() == TokenUsage(prompt=1200, cache_read=1000, output=300)
    assert reader.complete


def test_a_plain_chat_answer_names_its_usage_on_the_body() -> None:
    body = json.dumps(
        {
            "choices": [{"message": {"content": "Hello"}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45},
        }
    ).encode()

    reader = _read(CHAT, body, content_type=_JSON, step=11)

    assert reader.usage() == TokenUsage(prompt=40, output=5)
    assert reader.complete


def test_a_gateway_that_names_cache_and_reasoning_beside_the_counts_is_read_too() -> None:
    # The same facts outside the ``…_details`` records, as some gateways write them.
    usage = {
        "prompt_tokens": 900,
        "completion_tokens": 50,
        "total_tokens": 1010,
        "reasoning_tokens": 60,
        "cache_read_tokens": 700,
        "cached_tokens": 700,
        "cache_write_tokens": 100,
    }

    reader = _read(CHAT, _sse({"choices": [], "usage": usage}, "[DONE]"), content_type=_EVENTS)

    # The total counts reasoning on top of the completion, so it is charged too.
    assert reader.usage() == TokenUsage(prompt=900, cache_read=700, cache_write=100, output=110)


@pytest.mark.parametrize(
    ("completion", "reasoning", "total", "billed"),
    [
        (300, 0, 1300, 300),
        # Included in the completion count.
        (300, 120, 1300, 300),
        # Counted on top of it.
        (300, 120, 1420, 420),
        # No total, or one that fits neither reading: the dearer one.
        (300, 120, None, 420),
        (300, 120, 9999, 420),
    ],
)
def test_reasoning_is_charged_once(completion, reasoning, total, billed) -> None:
    assert (
        billable_output(prompt=1000, completion=completion, reasoning=reasoning, total=total)
        == billed
    )


# -- Responses -----------------------------------------------------------------

_RESPONSE_USAGE = {
    "input_tokens": 5000,
    "input_tokens_details": {"cached_tokens": 4000},
    "output_tokens": 800,
    "output_tokens_details": {"reasoning_tokens": 500},
    "total_tokens": 5800,
}


@pytest.mark.parametrize("ending", ["response.completed", "response.incomplete"])
def test_a_responses_stream_names_its_usage_on_the_event_that_ends_it(ending) -> None:
    stream = _sse(
        {"type": "response.created", "response": {"id": "resp_1", "usage": None}},
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": ending, "response": {"id": "resp_1", "usage": _RESPONSE_USAGE}},
    )

    reader = _read(RESPONSES, stream, content_type=_EVENTS, step=13)

    assert reader.usage() == TokenUsage(prompt=5000, cache_read=4000, output=800)
    assert reader.complete


def test_a_plain_responses_answer_names_its_usage_on_the_body() -> None:
    body = json.dumps({"id": "resp_1", "output": [], "usage": _RESPONSE_USAGE}).encode()

    reader = _read(RESPONSES, body, content_type=_JSON)

    assert reader.usage() == TokenUsage(prompt=5000, cache_read=4000, output=800)
    assert reader.complete


# -- Messages ------------------------------------------------------------------

_MESSAGE_START = {
    "type": "message_start",
    "message": {
        "id": "msg_1",
        "usage": {
            "input_tokens": 30,
            "cache_read_input_tokens": 9000,
            "cache_creation_input_tokens": 500,
            "output_tokens": 1,
        },
    },
}


@pytest.mark.parametrize("ending", ["\n\n", "\r\n\r\n"])
def test_a_messages_stream_names_its_input_first_and_its_output_last(ending) -> None:
    stream = _sse(
        _MESSAGE_START,
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 420}},
        {"type": "message_stop"},
        ending=ending,
    )

    reader = _read(MESSAGES, stream, content_type=_EVENTS, step=17)

    # Its input count leaves the cached tokens out; a price list counts them in.
    assert reader.usage() == TokenUsage(prompt=9530, cache_read=9000, cache_write=500, output=420)
    assert reader.complete


def test_a_plain_messages_answer_names_its_usage_on_the_body() -> None:
    body = json.dumps(
        {"type": "message", "usage": {"input_tokens": 12, "output_tokens": 7}}
    ).encode()

    reader = _read(MESSAGES, body, content_type=_JSON)

    assert reader.usage() == TokenUsage(prompt=12, output=7)
    assert reader.complete


# -- answers that end early, or name nothing ----------------------------------


def test_a_messages_stream_cut_short_has_named_its_input_but_is_not_complete() -> None:
    stream = _sse(
        _MESSAGE_START,
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
    )

    reader = _read(MESSAGES, stream, content_type=_EVENTS)

    assert reader.usage() == TokenUsage(prompt=9530, cache_read=9000, cache_write=500, output=1)
    assert not reader.complete


@pytest.mark.parametrize(
    ("api", "body", "content_type"),
    [
        # A stream that ended before its usage chunk, and one that never sends one.
        (CHAT, _sse({"choices": [{"delta": {"content": "Hel"}}]}), _EVENTS),
        (CHAT, _sse({"choices": [{"delta": {"content": "Hello"}}]}, "[DONE]"), _EVENTS),
        (RESPONSES, _sse({"type": "response.output_text.delta", "delta": "Hi"}), _EVENTS),
        # A plain answer cut off in the middle, one with no usage, and one that is not JSON.
        (CHAT, b'{"choices": [{"message": {"content": "Hel', _JSON),
        (CHAT, b'{"choices": []}', _JSON),
        (CHAT, b"<html>gateway timeout</html>", "text/html"),
        (CHAT, b"", _JSON),
        # A usage record that names nothing a count can be read from.
        (CHAT, b'{"usage": {"prompt_tokens": "many", "completion_tokens": -4}}', _JSON),
        (CHAT, b'{"usage": {"prompt_tokens": true}}', _JSON),
    ],
)
def test_an_answer_that_names_no_usage_has_none(api, body, content_type) -> None:
    reader = _read(api, body, content_type=content_type)

    assert reader.usage() is None
    assert not reader.complete


def test_an_encoded_answer_is_not_read() -> None:
    body = json.dumps({"usage": {"prompt_tokens": 40, "completion_tokens": 5}}).encode()
    for encoding in ("gzip", "br", "zstd"):
        reader = UsageReader(CHAT, content_type=_JSON, content_encoding=encoding)
        reader.feed(body)
        assert reader.usage() is None
    reader = UsageReader(CHAT, content_type=_JSON, content_encoding="identity")
    reader.feed(body)
    assert reader.usage() == TokenUsage(prompt=40, output=5)


def test_a_line_too_long_to_keep_is_let_go_by_and_the_next_one_is_read(monkeypatch) -> None:
    monkeypatch.setattr(model_usage, "MAX_READ_BYTES", 256)
    long_event = {"choices": [{"delta": {"content": "x" * 600}}], "usage": {"prompt_tokens": 1}}
    stream = _sse(long_event, {"choices": [], "usage": {"prompt_tokens": 9}}, "[DONE]")

    reader = _read(CHAT, stream, content_type=_EVENTS, step=50)

    # The long line was not held, so what it said is not known; the short one is.
    assert reader.usage() == TokenUsage(prompt=9)
    assert reader.complete


def test_a_plain_answer_too_large_to_keep_is_not_read(monkeypatch) -> None:
    monkeypatch.setattr(model_usage, "MAX_READ_BYTES", 64)
    body = json.dumps(
        {"choices": [{"message": {"content": "y" * 200}}], "usage": {"prompt_tokens": 3}}
    ).encode()

    reader = _read(CHAT, body, content_type=_JSON, step=16)

    assert reader.usage() is None
    assert not reader.complete


def test_usage_text_inside_an_answer_is_not_mistaken_for_the_record() -> None:
    # The model wrote JSON that looks like a usage record; it arrives escaped, as text.
    said = json.dumps({"usage": {"prompt_tokens": 999999}})
    stream = _sse(
        {"choices": [{"delta": {"content": said}}]},
        {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}},
        "[DONE]",
    )

    reader = _read(CHAT, stream, content_type=_EVENTS)

    assert reader.usage() == TokenUsage(prompt=20, output=4)


def test_reading_never_fails_the_answer_it_reads(monkeypatch) -> None:
    deep = b"[" * 100_000 + b"]" * 100_000
    reader = _read(CHAT, deep, content_type=_JSON)
    assert reader.usage() is None and not reader.complete
    nested = b"data: " + b'{"usage":' * 50_000 + b"1" + b"}" * 50_000 + b"\n\n"
    reader = _read(CHAT, nested, content_type=_EVENTS)
    assert reader.usage() is None

    # Whatever goes wrong inside it, the reader gives up rather than raise.
    reader = UsageReader(CHAT, content_type=_EVENTS)

    def broken(_self: UsageReader) -> None:
        raise RuntimeError("a bug in the reader")

    monkeypatch.setattr(UsageReader, "_line", broken)
    reader.feed(_CHAT_STREAM)
    reader.feed(_CHAT_STREAM)
    assert reader.usage() is None and not reader.complete
