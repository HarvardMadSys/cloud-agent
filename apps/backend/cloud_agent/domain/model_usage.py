"""What a model request used, read from the provider's own answer.

Each of the three inference APIs an agent CLI speaks reports the tokens a
request used in the answer itself: a ``usage`` record in a plain JSON answer,
and in a stream the last events before it ends. :class:`UsageReader` is fed
an answer's bytes as they go by and keeps that record. It stores nothing else
of the answer: one unfinished line of an event stream, or a plain JSON answer
up to a bound, held only until it has been read.

Pure rules, no I/O. The three shapes:

- **Chat Completions**: ``usage`` on the body, or on a chunk of the stream.
  ``prompt_tokens`` counts every input token, the cached ones included.
- **Responses**: ``usage`` on the body, or on the ``response`` of the event
  that ends the stream. ``input_tokens`` likewise counts cached tokens.
- **Messages**: ``usage`` on the body; in a stream the input side arrives on
  ``message_start`` and the output count on ``message_delta``.
  ``input_tokens`` there is what was *not* served from or written to the
  cache, and the two cache counts stand beside it.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The inference API an answer belongs to.
CHAT = "chat"
RESPONSES = "responses"
MESSAGES = "messages"

#: The longest event line, and the largest plain JSON answer, kept to be read.
#: The event that ends a Responses stream carries the whole response; anything
#: longer than this is passed on unread and its request is charged by estimate.
MAX_READ_BYTES = 8 * 1024 * 1024

#: The events that end a Responses stream. Each carries the response, with its usage.
_RESPONSE_ENDS = frozenset({"response.completed", "response.incomplete", "response.failed"})


@dataclass(frozen=True)
class TokenUsage:
    """The tokens one request used, in the terms a price list is written in."""

    #: Every input token, the cached ones included.
    prompt: int = 0
    #: Of ``prompt``: tokens the provider read from its prompt cache.
    cache_read: int = 0
    #: Of ``prompt``: tokens the provider wrote to its prompt cache.
    cache_write: int = 0
    #: Output tokens, reasoning included.
    output: int = 0


def billable_output(*, prompt: int, completion: int, reasoning: int, total: int | None) -> int:
    """The output tokens to charge for.

    Providers disagree on whether an answer's completion count includes its
    reasoning tokens. Where the total shows that it does, reasoning is not
    added a second time; where the total is absent or says otherwise, it is
    added, which is the dearer reading.
    """
    if reasoning <= 0:
        return completion
    if total is not None and total == prompt + completion:
        return completion
    return completion + reasoning


def _count(value: Any) -> int | None:
    """A token count as a provider wrote it, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value >= 0 and value == int(value):
        return int(value)
    return None


def _first(*values: Any) -> int | None:
    """The first of ``values`` that is a token count."""
    for value in values:
        count = _count(value)
        if count is not None:
            return count
    return None


def _details(usage: dict[str, Any], name: str) -> dict[str, Any]:
    found = usage.get(name)
    return found if isinstance(found, dict) else {}


class UsageReader:
    """Find the usage record of one answer as its bytes go by.

    ``api`` is the inference API the request was made on. An answer in a
    content encoding other than identity is not read: the relay asks every
    provider for none, and one that compresses anyway is charged by estimate.

    Reading never fails the answer it reads. Whatever an answer holds, the
    worst that follows is that it names no usage.
    """

    def __init__(self, api: str, *, content_type: str = "", content_encoding: str = "") -> None:
        """Start reading an answer with these headers."""
        self._api = api
        self._readable = content_encoding.strip().lower() in ("", "identity")
        self._events = content_type.split(";", 1)[0].strip().lower() == "text/event-stream"
        self._kept = bytearray()
        #: Inside something too long to keep: the rest of it is let go by.
        self._overflowed = False
        self._counts: dict[str, int] = {}
        self._ended = False
        self._body_read = False

    def feed(self, chunk: bytes) -> None:
        """Take the next bytes of the answer."""
        if not self._readable or not chunk:
            return
        try:
            self._feed(chunk)
        except Exception:
            self._give_up()

    def _feed(self, chunk: bytes) -> None:
        if not self._events:
            self._keep(chunk)
            return
        start = 0
        while True:
            end = chunk.find(b"\n", start)
            if end < 0:
                self._keep(chunk[start:])
                return
            self._keep(chunk[start:end])
            self._line()
            start = end + 1

    def _give_up(self) -> None:
        """Stop reading an answer that could not be read; it names no usage."""
        self._readable = False
        self._kept.clear()
        self._counts.clear()
        self._ended = False

    def usage(self) -> TokenUsage | None:
        """What the answer said it used so far; ``None`` when it named nothing."""
        try:
            self._read_body()
        except Exception:
            self._give_up()
        counts = self._counts
        if not counts:
            return None
        read, write = counts.get("cache_read", 0), counts.get("cache_write", 0)
        if self._api == MESSAGES:
            return TokenUsage(
                prompt=counts.get("input", 0) + read + write,
                cache_read=read,
                cache_write=write,
                output=counts.get("output", 0),
            )
        prompt = counts.get("prompt", 0)
        return TokenUsage(
            prompt=prompt,
            cache_read=read,
            cache_write=write,
            output=billable_output(
                prompt=prompt,
                completion=counts.get("completion", 0),
                reasoning=counts.get("reasoning", 0),
                total=counts.get("total"),
            ),
        )

    @property
    def complete(self) -> bool:
        """Whether the answer was read to its end and named what it used.

        An answer cut short may have named part of it: a Messages stream gives
        its input on the first event. That part is still what :meth:`usage`
        answers, and this says it is not the whole.
        """
        if self.usage() is None:
            return False
        return self._ended

    # -- internals -------------------------------------------------------------

    def _keep(self, piece: bytes) -> None:
        if self._overflowed:
            return
        if len(self._kept) + len(piece) > MAX_READ_BYTES:
            self._overflowed = True
            self._kept.clear()
            return
        self._kept += piece

    def _line(self) -> None:
        """Read one finished line of an event stream."""
        line, overflowed = bytes(self._kept), self._overflowed
        self._kept.clear()
        self._overflowed = False
        if overflowed or not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]":
            self._ended = True
            return
        # Nearly every event is a piece of the answer's text. Only the few that
        # can carry usage, or end a Messages stream, are decoded.
        if b'"usage"' not in data and b'"message_stop"' not in data:
            return
        try:
            event = json.loads(data)
        except (ValueError, RecursionError):
            return
        if isinstance(event, dict):
            self._event(event)

    def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if self._api == MESSAGES:
            if kind == "message_stop":
                self._ended = True
                return
            message = event.get("message")
            self._record(message.get("usage") if isinstance(message, dict) else event.get("usage"))
        elif self._api == RESPONSES:
            response = event.get("response")
            if isinstance(response, dict):
                self._record(response.get("usage"))
                if kind in _RESPONSE_ENDS:
                    self._ended = True
        else:
            self._record(event.get("usage"))

    def _read_body(self) -> None:
        """Read a plain JSON answer, once, when it is asked about."""
        if self._events or self._body_read or not self._readable:
            return
        self._body_read = True
        body, overflowed = bytes(self._kept), self._overflowed
        self._kept.clear()
        if overflowed or not body:
            return
        try:
            document = json.loads(body)
        except (ValueError, RecursionError):
            return
        if isinstance(document, dict):
            self._ended = True
            self._record(document.get("usage"))

    def _record(self, usage: Any) -> None:
        """Keep the counts a usage record names; a later record replaces what it repeats."""
        if not isinstance(usage, dict):
            return
        if self._api == MESSAGES:
            found = {
                "input": _count(usage.get("input_tokens")),
                "output": _count(usage.get("output_tokens")),
                "cache_read": _count(usage.get("cache_read_input_tokens")),
                "cache_write": _count(usage.get("cache_creation_input_tokens")),
            }
        elif self._api == RESPONSES:
            given, made = (
                _details(usage, "input_tokens_details"),
                _details(usage, "output_tokens_details"),
            )
            found = {
                "prompt": _count(usage.get("input_tokens")),
                "completion": _count(usage.get("output_tokens")),
                "total": _count(usage.get("total_tokens")),
                "reasoning": _first(made.get("reasoning_tokens"), usage.get("reasoning_tokens")),
                "cache_read": _first(given.get("cached_tokens"), usage.get("cache_read_tokens")),
                "cache_write": _first(
                    given.get("cache_write_tokens"), usage.get("cache_write_tokens")
                ),
            }
        else:
            given, made = (
                _details(usage, "prompt_tokens_details"),
                _details(usage, "completion_tokens_details"),
            )
            found = {
                "prompt": _count(usage.get("prompt_tokens")),
                "completion": _count(usage.get("completion_tokens")),
                "total": _count(usage.get("total_tokens")),
                "reasoning": _first(made.get("reasoning_tokens"), usage.get("reasoning_tokens")),
                "cache_read": _first(
                    given.get("cached_tokens"),
                    usage.get("cache_read_tokens"),
                    usage.get("cached_tokens"),
                ),
                "cache_write": _first(
                    given.get("cache_write_tokens"), usage.get("cache_write_tokens")
                ),
            }
        self._counts.update({name: count for name, count in found.items() if count is not None})


__all__ = [
    "CHAT",
    "MAX_READ_BYTES",
    "MESSAGES",
    "RESPONSES",
    "TokenUsage",
    "UsageReader",
    "billable_output",
]
