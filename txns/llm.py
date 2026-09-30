"""The one connection `author` uses to reach the LLM (FR-C1, test seam 2).

    transport.send(Request(...)) -> Response(text, model, cost_usd)

A request goes in; the response text, the model the provider reports and the
cost of the call come back. A failed call raises `TransportError`. Everything
`author` knows about the network stops here: tests inject a scripted fake
(`main(..., transport=fake)`), and `connect` gives the real OpenRouter
connection (ticket 16). A request carries only fixed instructions, data taken
from the scrubbed payload and earlier drafts; `author` leak-checks the whole
request before it is sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from txns.errors import LLMUnreachable, MissingInput


@dataclass(frozen=True)
class Request:
    part: str  # the draft part this call produces, e.g. "catalog-02"
    model: str | None  # the pinned slug (`author.model`); the connection must use exactly this
    instructions: str  # fixed text written by txns (the system message)
    input: Mapping[str, Any]  # data for this part: scrubbed payload extracts and earlier drafts
    schema: Mapping[str, Any] = field(default_factory=dict)  # JSON schema the response must match

    def content(self) -> dict[str, Any]:
        """Everything the call sends besides the model slug (what the leak check reads)."""
        return {"instructions": self.instructions, "input": self.input, "schema": self.schema}

    def user_message(self) -> str:
        return json.dumps(self.input, ensure_ascii=False, sort_keys=True, indent=1)


@dataclass(frozen=True)
class Response:
    text: str  # the model's answer, expected to be one JSON document
    model: str  # the model the provider reports having served (FR-C8)
    cost_usd: float  # what this call cost (FR-C6)


class TransportError(Exception):
    """A call that did not produce a response (network error, rate limit, retired model...).

    `retryable` tells the caller whether trying again can help (ticket 16 retries
    network errors and rate limits; a retired model slug is not retryable)."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class Transport(Protocol):
    def send(self, request: Request) -> Response: ...


def connect(settings: Mapping[str, Any], env: Mapping[str, str]) -> Transport:
    """The real connection for `author.model` (OpenRouter, ticket 16)."""
    if not settings.get("model"):
        raise MissingInput("`author.model` is not set; `txns author` needs one pinned OpenRouter model slug")
    raise LLMUnreachable("the OpenRouter connection is not built yet; no LLM call made")
