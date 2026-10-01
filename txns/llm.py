"""The one connection `author` uses to reach the LLM (FR-C1, FR-C4, FR-C8, test seam 2).

    transport.send(Request(...)) -> Response(text, model, cost_usd)
    send_with_retries(transport, request, sleep=..., on_retry=...) -> Response

A request goes in; the response text, the model the provider reports and the
cost of the call come back. A failed call raises `TransportError`, marked
retryable (network error, timeout, rate limit, provider overloaded) or not
(retired model slug, rejected key, bad request). Everything `author` knows about
the network stops here: tests inject a scripted fake (`main(..., transport=fake)`),
and `connect` gives the real OpenRouter connection. A request carries only fixed
instructions, data taken from the scrubbed payload and earlier drafts (and, on a
re-ask, the rejected answer and its problems); `author` leak-checks the whole
request before it is sent.

OpenRouter (`OpenRouter`, stdlib `urllib` only): one POST per call to
`/chat/completions` with exactly `author.model` (no `models` fallback list, no
`route`), `provider.data_collection = "deny"`, the part's JSON schema both as
`response_format` and in the system message, and `usage.include` so the
response reports its cost. The API key is read only from `OPENROUTER_API_KEY`
and goes only into the Authorization header; no message, file or exception
carries it.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

from txns import __version__
from txns.errors import MissingInput

API_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_S = 300  # a long variants draft can take minutes to write
RETRIES = 3  # FR-C4: a failing call is tried 1 + 3 times
BACKOFF_S = 2.0  # waits 2, 4, 8 s (exponential); a longer Retry-After is honoured
MAX_WAIT_S = 60.0
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529})
MAX_ERROR_TEXT = 300


@dataclass(frozen=True)
class Request:
    part: str  # the draft part this call produces, e.g. "catalog-02"
    model: str | None  # the pinned slug (`author.model`); the connection must use exactly this
    instructions: str  # fixed text written by txns (the system message)
    input: Mapping[str, Any]  # data for this part: scrubbed payload extracts and earlier drafts
    schema: Mapping[str, Any] = field(default_factory=dict)  # JSON schema the response must match
    # A re-ask (FR-C5): what was wrong with the first answer, and that answer when it may be sent back.
    problems: tuple[str, ...] = ()
    previous: str | None = None

    def content(self) -> dict[str, Any]:
        """Everything the call sends besides the model slug (what the leak check reads)."""
        out: dict[str, Any] = {"instructions": self.instructions, "input": self.input, "schema": self.schema}
        if self.problems:
            out["problems"] = list(self.problems)
        if self.previous is not None:
            out["previous"] = self.previous
        return out

    def user_message(self) -> str:
        return json.dumps(self.input, ensure_ascii=False, sort_keys=True, indent=1)

    def reask(self, problems: list[str], previous: str | None) -> "Request":
        return Request(self.part, self.model, self.instructions, self.input, self.schema,
                       tuple(problems), previous)

    def messages(self) -> list[dict[str, str]]:
        """The chat messages this request sends: system (instructions + schema), user (input), re-ask."""
        system = (self.instructions + "\n\nThe answer must be one JSON document matching this JSON schema:\n"
                  + json.dumps(self.schema, ensure_ascii=False, sort_keys=True))
        out = [{"role": "system", "content": system}, {"role": "user", "content": self.user_message()}]
        if self.problems:
            if self.previous is not None:
                out.append({"role": "assistant", "content": self.previous})
            where = "Your previous answer" if self.previous is not None else "Your previous answer (not repeated here)"
            out.append({"role": "user", "content": (
                f"{where} failed validation:\n" + "\n".join(f"- {p}" for p in self.problems)
                + "\nAnswer again with the whole corrected JSON document only. Where a problem says a text "
                "carries a ledger name, rewrite that text without any person or business name."
            )})
        return out


@dataclass(frozen=True)
class Response:
    text: str  # the model's answer, expected to be one JSON document
    model: str  # the model the provider reports having served (FR-C8)
    cost_usd: float  # what this call cost (FR-C6)


class TransportError(Exception):
    """A call that did not produce a response (network error, rate limit, retired model...).

    `retryable` tells the caller whether trying again can help: network errors and
    rate limits are retried (FR-C4); a retired model slug is not. `retry_after` is
    the wait in seconds the provider asked for, if any."""

    def __init__(self, message: str, *, retryable: bool = True, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.attempts = 1  # set by send_with_retries


class Transport(Protocol):
    def send(self, request: Request) -> Response: ...


def backoff(retry: int, retry_after: float | None = None) -> float:
    """Seconds to wait before retry number `retry` (1-based): 2, 4, 8 ... or a longer Retry-After."""
    wait = BACKOFF_S * 2 ** (retry - 1)
    if retry_after is not None and retry_after > wait:
        wait = min(float(retry_after), MAX_WAIT_S)
    return wait


def send_with_retries(
    transport: Transport,
    request: Request,
    *,
    sleep: Callable[[float], None],
    on_retry: Callable[[int, float, TransportError], None] = lambda n, wait, exc: None,
) -> Response:
    """Send, retrying a retryable failure RETRIES times with exponential backoff (FR-C4, T34).

    The last TransportError is raised with `.attempts` set."""
    retry = 0
    while True:
        try:
            return transport.send(request)
        except TransportError as exc:
            exc.attempts = retry + 1
            if not exc.retryable or retry >= RETRIES:
                raise
            retry += 1
            wait = backoff(retry, exc.retry_after)
            on_retry(retry, wait, exc)
            sleep(wait)


def connect(settings: Mapping[str, Any], env: Mapping[str, str]) -> Transport:
    """The real connection for `author.model`: OpenRouter, key from OPENROUTER_API_KEY only."""
    if not settings.get("model"):
        raise MissingInput("`author.model` is not set; `txns author` needs one pinned OpenRouter model slug")
    key = env.get(API_KEY_ENV, "").strip()
    if not key:
        raise MissingInput(f"{API_KEY_ENV} is not set; `txns author` needs an OpenRouter API key")
    return OpenRouter(str(settings["model"]), key)


class OpenRouter:
    """OpenRouter chat completions for one pinned model slug (FR-C1)."""

    def __init__(self, model: str, api_key: str, *, url: str = OPENROUTER_URL, timeout: float = TIMEOUT_S):
        self.model = model
        self._key = api_key
        self.url = url
        self.timeout = timeout

    def __repr__(self) -> str:  # never show the key
        return f"OpenRouter(model={self.model!r})"

    def body(self, request: Request) -> dict[str, Any]:
        """The JSON body of the call (no key: that goes in the Authorization header only)."""
        if request.model != self.model:
            raise ValueError(f"request for model {request.model!r} on a connection pinned to {self.model!r}")
        return {
            "model": self.model,  # exactly the pinned slug; no `models` list, so no fallback model
            "provider": {"data_collection": "deny"},
            "messages": request.messages(),
            "response_format": {
                "type": "json_schema",
                # Not strict: the draft schemas use keywords (minItems, pattern, ...) that strict
                # modes reject. txns checks the answer itself and re-asks once (FR-C5).
                "json_schema": {"name": request.part.split("-")[0], "strict": False, "schema": request.schema},
            },
            "usage": {"include": True},  # report the cost of this call (FR-C6)
        }

    def send(self, request: Request) -> Response:
        data = json.dumps(self.body(request), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "X-Title": f"txns {__version__}",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc.code, _read(exc), exc.headers) from None
        except (urllib.error.URLError, http.client.HTTPException, socket.timeout, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise TransportError(self._clean(f"network error: {reason}")) from None
        if status >= 400:  # urlopen raises for these; kept for stubs that return them
            raise self._http_error(status, raw, None)
        return self._response(raw)

    def _response(self, raw: bytes) -> Response:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise TransportError("OpenRouter sent a response that is not JSON") from None
        if not isinstance(data, dict):
            raise TransportError("OpenRouter sent a response that is not a JSON object")
        if isinstance(data.get("error"), dict):  # an error reported with HTTP 200
            code = data["error"].get("code")
            raise self._http_error(code if isinstance(code, int) else 502, raw, None)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise TransportError("OpenRouter sent a response without choices")
        choice = choices[0]
        if isinstance(choice.get("error"), dict):
            code = choice["error"].get("code")
            raise TransportError(self._clean(
                f"the provider failed mid-answer: {choice['error'].get('message', '')}"),
                retryable=not isinstance(code, int) or code in RETRYABLE_STATUS or code >= 500)
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost = usage.get("cost")
        return Response(
            text=content if isinstance(content, str) else "",
            model=str(data.get("model") or self.model),
            cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
        )

    def _http_error(self, status: int, raw: bytes, headers) -> TransportError:
        message = _error_message(raw)
        retry_after = _retry_after(headers)
        detail = f" ({self._clean(message)})" if message else ""
        if status in RETRYABLE_STATUS or status >= 500:
            what = "rate limited" if status == 429 else "OpenRouter or the provider is unavailable"
            return TransportError(f"{what}: HTTP {status}{detail}", retry_after=retry_after)
        if status == 404 and _DATA_POLICY.search(message):
            return TransportError(
                f"no provider of model `{self.model}` accepts the data-collection denial (HTTP 404{detail}); "
                "pick a model with a provider that does not train on prompts", retryable=False)
        if _retired(status, message):
            return TransportError(
                f"OpenRouter does not serve model `{self.model}` (HTTP {status}{detail}); the slug is retired "
                "or misspelled: set `author.model` to a current OpenRouter slug", retryable=False)
        if status == 401:
            return TransportError(f"OpenRouter rejected the API key in {API_KEY_ENV} (HTTP 401{detail})",
                                  retryable=False)
        if status == 402:
            return TransportError(f"the OpenRouter account is out of credits (HTTP 402{detail})", retryable=False)
        return TransportError(f"OpenRouter refused the request: HTTP {status}{detail}", retryable=False)

    def _clean(self, text: str) -> str:
        """An error text safe to print: no key, one line, not too long."""
        text = " ".join(str(text).replace(self._key, "[key]").split())
        return text if len(text) <= MAX_ERROR_TEXT else text[: MAX_ERROR_TEXT - 3] + "..."


_RETIRED = re.compile(
    r"not a valid model|model.{0,80}(?:not found|does not exist|no longer|deprecated|retired|unavailable|unknown)"
    r"|(?:unknown|invalid|deprecated|retired) model|no endpoints found for",
    re.I,
)


_DATA_POLICY = re.compile(r"data policy|data.collection|privacy", re.I)


def _retired(status: int, message: str) -> bool:
    if status == 404 and not _DATA_POLICY.search(message) and "parameters" not in message.casefold():
        return True
    return status in (400, 404, 410) and bool(_RETIRED.search(message))


def _read(exc: urllib.error.HTTPError) -> bytes:
    try:
        return exc.read() or b""
    except (OSError, http.client.HTTPException):
        return b""


def _error_message(raw: bytes) -> str:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw.decode("utf-8", errors="replace").strip()[:MAX_ERROR_TEXT]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or "")
    return str(err or "")


def _retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
