"""What a model request used, read from the provider's own answer, or estimated.

Each of the three inference APIs an agent CLI speaks reports the tokens a
request used in the answer itself: a ``usage`` record in a plain JSON answer,
and in a stream the last events before it ends. :class:`UsageReader` is fed
an answer's bytes as they go by and keeps that record, and the size of the
text the answer produced on the way. It keeps nothing else of the answer: one
unfinished line of an event stream, or a plain JSON answer up to a bound,
held only until it has been read.

Pure rules, no I/O. The three shapes:

- **Chat Completions**: ``usage`` on the body, or on a chunk of the stream.
  ``prompt_tokens`` counts every input token, the cached ones included.
- **Responses**: ``usage`` on the body, or on the ``response`` of the event
  that ends the stream. ``input_tokens`` likewise counts cached tokens.
- **Messages**: ``usage`` on the body; in a stream the input side arrives on
  ``message_start`` and the output count on ``message_delta``.
  ``input_tokens`` there is what was *not* served from or written to the
  cache, and the two cache counts stand beside it.

**Estimates.** An answer that names no usage, or is cut short before it does,
is charged by estimate, the way an inference gateway bills a stream its
client closed (hybridInference's ``StreamSession._cancelled_usage``): the
request's text as input, and the text the answer produced so far as output.
Text is one token per four bytes, the gateway's own fallback when it has no
tokenizer; an image, an audio clip or a video is a flat number of tokens,
never the size of its bytes.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

#: The inference API an answer belongs to.
CHAT = "chat"
RESPONSES = "responses"
MESSAGES = "messages"

#: The longest event line, and the largest plain JSON answer, kept to be read.
#: The event that ends a Responses stream carries the whole response; anything
#: longer than this is passed on unread and its request is charged by estimate.
MAX_READ_BYTES = 8 * 1024 * 1024

#: Bytes of text taken as one token when no count is to be had.
BYTES_PER_ESTIMATED_TOKEN = 4
#: What one media block of a request is taken to be in input tokens, as the
#: gateway estimates them (``serving.utils.tokens``): a flat amount per image,
#: audio clip and video, whatever the size of its bytes.
IMAGE_TOKEN_ESTIMATE = 85
AUDIO_TOKEN_ESTIMATE = 200
VIDEO_TOKEN_ESTIMATE = 1000
_MEDIA_BLOCKS = {
    "image_url": IMAGE_TOKEN_ESTIMATE,
    "image": IMAGE_TOKEN_ESTIMATE,
    "input_image": IMAGE_TOKEN_ESTIMATE,
    "input_audio": AUDIO_TOKEN_ESTIMATE,
    "audio": AUDIO_TOKEN_ESTIMATE,
    "video_url": VIDEO_TOKEN_ESTIMATE,
    "video": VIDEO_TOKEN_ESTIMATE,
    "input_video": VIDEO_TOKEN_ESTIMATE,
}
#: A string at least this long with no whitespace in it is a file in base64,
#: not text. Outside a media block it is taken to be an image.
_EMBEDDED_MIN_CHARS = 4096

#: The events that end a Responses stream. Each carries the response, with its usage.
_RESPONSE_ENDS = frozenset({"response.completed", "response.incomplete", "response.failed"})
#: What a Chat Completions delta writes as output text.
_CHAT_DELTA_TEXT = ("content", "reasoning_content", "reasoning", "refusal")
#: What a Messages ``content_block_delta`` writes as output text.
_MESSAGES_DELTA_TEXT = ("text", "thinking", "partial_json")


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
    added, which is the dearer reading. The gateway's rule
    (``serving.storage.utils.billable_output_tokens``).
    """
    if reasoning <= 0:
        return completion
    if total is not None and total == prompt + completion:
        return completion
    return completion + reasoning


def estimated_text_tokens(text_bytes: int) -> int:
    """The tokens this many bytes of text are taken to be."""
    return -(-max(text_bytes, 0) // BYTES_PER_ESTIMATED_TOKEN)


def estimated_prompt_tokens(document: Any) -> int:
    """The input tokens a request is taken to be, from its decoded JSON body.

    The text in it, keys and string values alike, and a flat amount for each
    image, audio clip or video, whether in a block of that type or as a file
    in base64 elsewhere. Counting a body's raw bytes would take a
    conversation's screenshots for megabytes of text. An estimate for when the
    provider's own count is not to be had, not a tokenizer: it knows nothing
    of a model's vocabulary or of what the provider served from its cache.
    """
    text = media = 0
    pending = [document]
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            if _embedded_file(node):
                media += IMAGE_TOKEN_ESTIMATE
            else:
                text += _text_bytes(node)
        elif isinstance(node, dict):
            kind = node.get("type")
            flat = _MEDIA_BLOCKS.get(kind) if isinstance(kind, str) else None
            if flat is not None:
                media += flat
                continue
            for key, value in node.items():
                text += _text_bytes(str(key))
                pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)
    return estimated_text_tokens(text) + media


def _text_bytes(value: str) -> int:
    return len(value.encode("utf-8", "surrogatepass"))


def _embedded_file(value: str) -> bool:
    """Whether a string in a request is a file's bytes rather than text."""
    if value.startswith("data:"):
        return True
    return len(value) >= _EMBEDDED_MIN_CHARS and not any(ch in value for ch in " \n\t")


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
        #: Bytes of text the answer produced so far: content, reasoning and tool calls.
        self._produced = 0
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

    @property
    def produced_tokens(self) -> int:
        """The output tokens the text this answer produced so far is taken to be."""
        return estimated_text_tokens(self._produced)

    def billed(self, estimated_prompt: int) -> tuple[TokenUsage, bool]:
        """What the request is charged for, and whether that is an estimate.

        What the answer named, when it was read to its end. Otherwise an
        estimate, as the gateway bills a stream cut off before its end: the
        input it named, or ``estimated_prompt`` when it named none, and the
        output it named or the text it produced, whichever is more.
        """
        usage = self.usage()
        if usage is None:
            return TokenUsage(prompt=estimated_prompt, output=self.produced_tokens), True
        if self._ended:
            return usage, False
        return replace(usage, output=max(usage.output, self.produced_tokens)), True

    # -- internals -------------------------------------------------------------

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
            self._messages_output(event)
            message = event.get("message")
            self._record(message.get("usage") if isinstance(message, dict) else event.get("usage"))
        elif self._api == RESPONSES:
            if isinstance(kind, str) and kind.endswith(".delta") and "audio" not in kind:
                self._produce(event.get("delta"))
            response = event.get("response")
            if isinstance(response, dict):
                self._record(response.get("usage"))
                if kind in _RESPONSE_ENDS:
                    self._ended = True
        else:
            self._chat_output(event)
            self._record(event.get("usage"))

    def _produce(self, text: Any) -> None:
        if isinstance(text, str):
            self._produced += _text_bytes(text)

    def _chat_output(self, event: dict[str, Any]) -> None:
        choices = event.get("choices")
        for choice in choices if isinstance(choices, list) else ():
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if not isinstance(delta, dict):
                continue
            for name in _CHAT_DELTA_TEXT:
                self._produce(delta.get(name))
            calls = delta.get("tool_calls")
            for call in calls if isinstance(calls, list) else ():
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict):
                    self._produce(function.get("name"))
                    self._produce(function.get("arguments"))

    def _messages_output(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict):
                for name in _MESSAGES_DELTA_TEXT:
                    self._produce(delta.get(name))
        elif kind == "content_block_start":
            block = event.get("content_block")
            if isinstance(block, dict):
                self._produce(block.get("name"))
                self._produce(block.get("text"))

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
                # The OpenAI detail, the gateway's own field, a bare field some
                # providers use, and DeepSeek's.
                "cache_read": _first(
                    given.get("cached_tokens"),
                    usage.get("cache_read_tokens"),
                    usage.get("cached_tokens"),
                    usage.get("prompt_cache_hit_tokens"),
                ),
                "cache_write": _first(
                    given.get("cache_write_tokens"), usage.get("cache_write_tokens")
                ),
            }
        self._counts.update({name: count for name, count in found.items() if count is not None})


__all__ = [
    "AUDIO_TOKEN_ESTIMATE",
    "BYTES_PER_ESTIMATED_TOKEN",
    "CHAT",
    "IMAGE_TOKEN_ESTIMATE",
    "MAX_READ_BYTES",
    "MESSAGES",
    "RESPONSES",
    "VIDEO_TOKEN_ESTIMATE",
    "TokenUsage",
    "UsageReader",
    "billable_output",
    "estimated_prompt_tokens",
    "estimated_text_tokens",
]
