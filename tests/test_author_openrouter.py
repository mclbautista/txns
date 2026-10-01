"""`txns author` talks to OpenRouter safely and cheaply (ticket 16: FR-C1, FR-C4 to FR-C8, T34-T36).

Two layers, neither touches the network:
- failure handling through the scripted fake transport (tests/llm_fake.py): retries
  with backoff, the one re-ask, the cost cap and resuming;
- the real OpenRouter transport (`txns.llm.OpenRouter`) with `urllib.request.urlopen`
  replaced by `FakeHTTP` below, and socket connects blocked as a safety net.

Retry waits are recorded (`ws.sleeps`, `sleep=`) instead of slept.
"""

import email.message
import io
import json
import socket
import unittest
import urllib.error
from datetime import date
from unittest import mock

from tests.helpers import Workspace
from tests.llm_fake import Fail, Reply, ScriptedLLM, valid_draft
from tests.test_author_drafts import AUTHOR_DIR, MODEL, PARTS, DraftCase
from tests.test_author_ledger import KEY
from txns import llm
from txns.cli import main

SECRET = KEY["OPENROUTER_API_KEY"]
SERVED = "example/pinned-model-20260901"


def _no_network(*args, **kwargs):
    raise AssertionError("a test tried to open a real network connection")


class RetryTest(DraftCase):
    def test_always_failing_transport_tries_4_times_then_exits_3(self):  # T34
        fake = ScriptedLLM().script("storylines", *[Fail("connection reset")] * 10)
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), ["storylines"] * 4)
        self.assertEqual(self.ws.sleeps, [2.0, 4.0, 8.0])  # exponential backoff
        self.assertIn("failed after 4 attempts: connection reset", r.stderr)
        for n in (1, 2, 3):
            self.assertIn(f"retry {n} of 3", r.stderr)
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_every_call_gets_its_own_retries(self):
        fake = ScriptedLLM().script("catalog-01", Fail("timeout"), Fail("timeout"))
        fake.script("vocabulary", Fail("rate limited"), Fail("rate limited"), Fail("rate limited"))
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("catalog-01"), 3)
        self.assertEqual(fake.parts().count("vocabulary"), 4)
        self.assertEqual(self.ws.sleeps, [2.0, 4.0, 2.0, 4.0, 8.0])
        self.assertIn("6 LLM calls this run", r.stdout)  # failed attempts are not calls (no response, no cost)

    def test_a_non_retryable_failure_exits_3_at_once(self):
        fake = ScriptedLLM().script("storylines", Fail("model `x/y` is retired", retryable=False))
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), ["storylines"])
        self.assertEqual(self.ws.sleeps, [])
        self.assertIn("model `x/y` is retired", r.stderr)

    def test_backoff_honours_a_longer_retry_after(self):
        self.assertEqual([llm.backoff(n) for n in (1, 2, 3)], [2.0, 4.0, 8.0])
        self.assertEqual(llm.backoff(1, 30), 30.0)
        self.assertEqual(llm.backoff(3, 5), 8.0)
        self.assertEqual(llm.backoff(1, 3600), llm.MAX_WAIT_S)


class ReaskTest(DraftCase):
    def test_one_invalid_answer_is_re_asked_with_the_error(self):  # T35
        fake = ScriptedLLM().script("vocabulary", Reply(document={"date_tails": ["{yyyy}"]}, cost=0.25))
        r = self.ok(fake)
        self.assertEqual(fake.parts(), PARTS + ["vocabulary"])
        first, again = fake.requests[-2:]
        self.assertEqual(first.problems, ())
        self.assertTrue(any("must name the day and the month" in p for p in again.problems), again.problems)
        self.assertEqual(json.loads(again.previous), {"date_tails": ["{yyyy}"]})
        self.assertEqual((again.input, again.schema, again.model), (first.input, first.schema, first.model))
        # The error and the rejected answer go out in the messages the real connection sends.
        messages = again.messages()
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertIn("must name the day and the month", messages[-1]["content"])
        self.assertIn("draft `vocabulary` failed its schema (1 problem); asking once more", r.stderr)
        self.assertEqual(self.saved("vocabulary")["cost_usd"], 0.26)  # both answers' cost
        self.assertIn("7 LLM calls this run ($0.3100)", r.stdout)

    def test_second_invalid_answer_exits_4(self):  # T35
        bad = Reply(text="Here you go!")
        fake = ScriptedLLM().script("catalog-01", bad, bad, Reply())
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), ["storylines", "catalog-01", "catalog-01"])
        self.assertIn("draft `catalog-01` failed its schema again after one re-ask", r.stderr)
        self.assertIn("not one JSON document", fake.requests[-1].problems[0])
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_a_re_ask_never_sends_back_a_ledger_name(self):
        leaky = Reply(edit=lambda d: d["items"][0]["descriptive"].append("Coffee run with Swiftlane Couriers"))
        fake = ScriptedLLM().script("variants-01", leaky)
        self.ok(fake)
        again = fake.requests[fake.parts().index("variants-01") + 1]
        self.assertEqual(again.part, "variants-01")
        self.assertIsNone(again.previous)  # the rejected answer carried the name: not sent back
        self.assertTrue(again.problems)
        self.assertTrue(all("carries a ledger name" in p for p in again.problems))
        self.assertNotIn("swiftlane", fake.sent_text().casefold())
        self.assertIn("not repeated here", again.messages()[-1]["content"])


class CostCapTest(DraftCase):
    def cap(self, usd):
        self.ws.write_config(f'[author]\nmodel = "{MODEL}"\nmax_cost_usd = {usd}\n')

    def test_reaching_the_cap_stops_keeps_drafts_and_resumes(self):  # T36
        self.cap(0.05)
        first = ScriptedLLM(cost=0.02)
        r = self.author(first)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(first.parts(), PARTS[:3])  # $0.06 >= $0.05 after the third call
        self.assertIn("cost cap reached", r.stderr)
        self.assertIn("$0.0600", r.stderr)
        self.assertIn("stopped before drafting `variants-01`", r.stderr)
        self.assertIn("3 valid drafts kept", r.stderr)
        self.assertEqual(self.saved_parts(), sorted(PARTS[:3]))
        self.assertFalse((self.ws.cwd / "bundles").exists())

        second = ScriptedLLM(cost=0.02)
        r = self.ok(second)
        self.assertEqual(second.parts(), PARTS[3:])  # no repeat of completed calls
        self.assertIn("3 LLM calls this run ($0.0600), 3 parts reused", r.stdout)
        self.assertEqual(self.saved_parts(), sorted(PARTS))

    def test_one_expensive_answer_stops_the_run(self):
        self.cap(1)
        fake = ScriptedLLM().script("storylines", Reply(cost=1.5))
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), ["storylines"])
        self.assertEqual(self.saved_parts(), ["storylines"])  # the paid-for draft is kept

    def test_the_cap_also_stops_a_re_ask(self):
        self.cap(0.5)
        fake = ScriptedLLM().script("storylines", Reply(text="nope", cost=0.5))
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), ["storylines"])
        self.assertIn("stopped before re-asking `storylines`", r.stderr)

    def test_default_cap_is_5_usd(self):
        fake = ScriptedLLM().script("storylines", Reply(cost=4.99)).script("catalog-01", Reply(cost=0.01))
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertEqual(fake.parts(), PARTS[:2])
        self.assertIn("`author.max_cost_usd` is $5", r.stderr)


class FakeHTTP:
    """Stands in for `urllib.request.urlopen`: records each call, answers from `handler`.

    `handler(body, headers)` returns (status, json-able body) or raises (an HTTPError or
    URLError built with `http_error` / `urllib.error.URLError`). Default: a valid draft.

    `like_anthropic=True` first refuses, the way Anthropic's structured-output validator
    does through OpenRouter, any request whose schema has a node with a list-valued `type`
    (HTTP 400 "Provider returned error", the provider's reason in `error.metadata.raw`)."""

    def __init__(self, handler=None, *, like_anthropic=False):
        self.calls: list[tuple[dict, dict]] = []
        self.handler = handler or self.answer
        self.like_anthropic = like_anthropic

    @staticmethod
    def request_of(body: dict) -> llm.Request:
        """Rebuild the draft request from the body (part kind from the schema name)."""
        kind = body["response_format"]["json_schema"]["name"]
        user = next(m for m in body["messages"] if m["role"] == "user")
        return llm.Request(part=kind, model=body["model"], instructions="", input=json.loads(user["content"]))

    def answer(self, body, headers):
        draft = valid_draft(self.request_of(body))
        return 200, {
            "id": "gen-1", "model": SERVED, "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.0123},
        }

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((body, headers))
        if self.like_anthropic:
            for where, kinds in _type_lists(body["response_format"]["json_schema"]["schema"]):
                raise http_error(400, "Provider returned error", provider="Anthropic", raw=anthropic_error(
                    f"output_format.schema{where}: declared type {kinds!r} is not supported"))
        status, data = self.handler(body, headers)
        return _Response(status, json.dumps(data).encode("utf-8"))


class _Response:
    def __init__(self, status, raw):
        self.status, self._raw = status, raw

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _type_lists(schema, where=""):
    """(path, kinds) of every schema node whose `type` is a list."""
    if isinstance(schema, dict):
        if isinstance(schema.get("type"), list):
            yield where or ".", schema["type"]
        for key, sub in schema.items():
            yield from _type_lists(sub, f"{where}.{key}")
    elif isinstance(schema, list):
        for i, sub in enumerate(schema):
            yield from _type_lists(sub, f"{where}[{i}]")


def anthropic_error(message: str) -> str:
    """An Anthropic API error body, as OpenRouter passes it on in `error.metadata.raw`."""
    return json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": message}})


def http_error(code: int, message: str, retry_after: str | None = None, *,
               raw: str | None = None, provider: str | None = None) -> urllib.error.HTTPError:
    """An OpenRouter error answer; `raw` / `provider` fill `error.metadata` (a provider's own error)."""
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    error = {"code": code, "message": message}
    if raw is not None:
        error["metadata"] = {"raw": raw} | ({"provider_name": provider} if provider else {})
    body = json.dumps({"error": error}).encode("utf-8")
    return urllib.error.HTTPError(llm.OPENROUTER_URL, code, "error", headers, io.BytesIO(body))


def failing(exc_factory):
    def handler(body, headers):
        raise exc_factory()
    return handler


class OpenRouterTest(unittest.TestCase):
    """The real connection, with the HTTP layer stubbed (no key is real, no socket opens)."""

    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_author_inputs()
        self.ws.write_config(f'[author]\nmodel = "{MODEL}"\n')
        self.sleeps: list[float] = []
        for target in (socket.socket, "connect"), (socket, "create_connection"):
            p = mock.patch.object(*target, _no_network)
            p.start()
            self.addCleanup(p.stop)

    def author(self, http: FakeHTTP, env=KEY):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(llm.urllib.request, "urlopen", http):
            code = main(["author"], today=date(2026, 10, 5), env=env, cwd=self.ws.cwd, stdout=out, stderr=err,
                        sleep=self.sleeps.append)  # no transport=: the real OpenRouter connection
        return code, out.getvalue(), err.getvalue()

    def test_requests_pin_the_slug_with_no_fallbacks_and_deny_data_collection(self):  # FR-C1
        http = FakeHTTP()
        code, out, err = self.author(http)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(http.calls), len(PARTS))
        for body, headers in http.calls:
            self.assertEqual(body["model"], MODEL)
            self.assertNotIn("models", body)  # no fallback model list
            self.assertNotIn("route", body)
            self.assertEqual(body["provider"], {"data_collection": "deny"})
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertTrue(body["response_format"]["json_schema"]["schema"])
            self.assertEqual(body["usage"], {"include": True})
            self.assertEqual(headers["authorization"], f"Bearer {SECRET}")
            self.assertNotIn(SECRET, json.dumps(body))

    def test_served_model_and_cost_are_recorded_per_response(self):  # FR-C8, FR-C6
        code, out, err = self.author(FakeHTTP())
        self.assertEqual(code, 0, out + err)
        drafts = next((self.ws.cwd / AUTHOR_DIR / "drafts").iterdir())
        for part in PARTS:
            saved = json.loads((drafts / f"{part}.json").read_text(encoding="utf-8"))
            self.assertEqual((saved["model"], saved["cost_usd"]), (SERVED, 0.0123))
        self.assertIn(f"served by: {SERVED}", out)
        self.assertIn("6 LLM calls this run ($0.0738)", out)

    def test_the_key_is_written_or_printed_nowhere(self):  # FR-C1
        def echo_key(body, headers):  # a provider error that quotes the Authorization header
            raise http_error(401, f"invalid key {headers['authorization']}")

        code, out, err = self.author(FakeHTTP(echo_key))
        self.assertEqual(code, 3, out + err)
        self.assertIn("rejected the API key in OPENROUTER_API_KEY", err)
        self.assertNotIn(SECRET, out + err)
        code, out, err = self.author(FakeHTTP())
        self.assertEqual(code, 0, out + err)
        self.assertNotIn(SECRET, out + err)
        for path in self.ws.cwd.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes(), path)
        self.assertNotIn(SECRET, repr(llm.connect({"model": MODEL}, KEY)))

    def test_network_errors_retry_3_times_then_exit_3(self):  # T34
        http = FakeHTTP(failing(lambda: urllib.error.URLError(ConnectionResetError("connection reset by peer"))))
        code, out, err = self.author(http)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(http.calls), 4)
        self.assertEqual(self.sleeps, [2.0, 4.0, 8.0])
        self.assertIn("network error", err)
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_rate_limits_retry_honouring_retry_after(self):  # T34
        http = FakeHTTP(failing(lambda: http_error(429, "Rate limit exceeded", retry_after="30")))
        code, out, err = self.author(http)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(http.calls), 4)
        self.assertEqual(self.sleeps, [30.0, 30.0, 30.0])
        self.assertIn("rate limited: HTTP 429", err)

    def test_a_rate_limit_that_clears_lets_the_run_finish(self):
        http = FakeHTTP()
        answers = [http_error(429, "slow down"), http_error(503, "overloaded")]

        def flaky(body, headers):
            if answers:
                raise answers.pop(0)
            return http.answer(body, headers)

        http.handler = flaky
        code, out, err = self.author(http)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(http.calls), len(PARTS) + 2)
        self.assertEqual(self.sleeps, [2.0, 4.0])

    def test_an_error_inside_a_200_answer_is_retried(self):
        http = FakeHTTP()
        answers = [{"error": {"code": 502, "message": "upstream provider error"}}]

        def flaky(body, headers):
            return (200, answers.pop(0)) if answers else http.answer(body, headers)

        http.handler = flaky
        code, out, err = self.author(http)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.sleeps, [2.0])

    def test_a_retired_model_slug_exits_3_with_a_clear_message(self):  # T34, FR-C4
        for status, message in ((404, f"Model {MODEL} not found"),
                                (400, f"{MODEL} is not a valid model ID"),
                                (404, "No endpoints found for " + MODEL)):
            with self.subTest(status=status, message=message):
                self.sleeps.clear()
                http = FakeHTTP(failing(lambda: http_error(status, message)))
                code, out, err = self.author(http)
                self.assertEqual(code, 3, out + err)
                self.assertEqual(len(http.calls), 1)  # not retried
                self.assertEqual(self.sleeps, [])
                self.assertIn(f"OpenRouter does not serve model `{MODEL}`", err)
                self.assertIn("set `author.model` to a current OpenRouter slug", err)

    def test_no_provider_accepting_the_data_policy_exits_3(self):
        http = FakeHTTP(failing(lambda: http_error(404, "No endpoints found matching your data policy")))
        code, out, err = self.author(http)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(http.calls), 1)
        self.assertIn("accepts the data-collection denial", err)

    def test_a_provider_that_refuses_type_lists_accepts_every_draft_schema(self):
        http = FakeHTTP(like_anthropic=True)
        code, out, err = self.author(http)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(http.calls), len(PARTS))
        self.assertRegex(out, r"promoted bundles/draft-[0-9a-f]{12}/")

    def test_a_provider_refusal_shows_the_provider_reason_and_exits_3(self):
        reason = "output_format.schema: Enum value 'stock' does not match declared type '['string', 'null']'"
        for raw in (anthropic_error(reason), reason):  # a JSON error object, or plain text
            with self.subTest(raw=raw):
                self.sleeps.clear()
                http = FakeHTTP(failing(lambda: http_error(400, "Provider returned error", raw=raw,
                                                           provider="Anthropic")))
                code, out, err = self.author(http)
                self.assertEqual(code, 3, out + err)
                self.assertEqual(len(http.calls), 1)  # a schema refusal is not retried
                self.assertEqual(self.sleeps, [])
                self.assertIn("OpenRouter refused the request: HTTP 400 (Provider returned error", err)
                self.assertIn(f"Anthropic: {reason}", err)
                self.assertNotIn('"invalid_request_error"', err)  # the innermost message, not the JSON

    def test_the_provider_reason_is_cleaned_like_other_error_text(self):
        raw = anthropic_error(f"bad\nrequest   with key {SECRET} " + "x" * 1000)
        http = FakeHTTP(failing(lambda: http_error(400, "Provider returned error", raw=raw)))
        code, out, err = self.author(http)
        self.assertEqual(code, 3, out + err)
        self.assertNotIn(SECRET, out + err)
        line = next(x for x in err.splitlines() if "OpenRouter refused the request" in x)
        self.assertIn("Provider returned error; bad request with key [key] xxx", line)
        self.assertIn("...)", line)  # cut to the error-text cap
        self.assertLess(len(line), llm.MAX_ERROR_TEXT + 200)

    def test_a_refusal_without_metadata_reads_as_before(self):
        http = FakeHTTP(failing(lambda: http_error(400, "Provider returned error")))
        code, out, err = self.author(http)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(http.calls), 1)
        self.assertIn("OpenRouter refused the request: HTTP 400 (Provider returned error)", err)

    def test_a_re_ask_goes_out_as_a_follow_up_message(self):  # T35 on the wire
        http = FakeHTTP()
        state = {"bad": 1}

        def once_bad(body, headers):
            if body["response_format"]["json_schema"]["name"] == "vocabulary" and state["bad"]:
                state["bad"] = 0
                return 200, {"model": SERVED, "choices": [{"message": {"content": "no JSON here"}}],
                             "usage": {"cost": 0.01}}
            return http.answer(body, headers)

        http.handler = once_bad
        code, out, err = self.author(http)
        self.assertEqual(code, 0, out + err)
        body = http.calls[-1][0]
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(body["messages"][2]["content"], "no JSON here")
        self.assertIn("not one JSON document", body["messages"][3]["content"])

    def test_missing_key_never_reaches_the_connection(self):  # T33
        http = FakeHTTP()
        code, out, err = self.author(http, env={})
        self.assertEqual(code, 2)
        self.assertEqual(http.calls, [])
        with self.assertRaises(Exception):
            llm.connect({"model": MODEL}, {"OPENROUTER_API_KEY": "  "})


if __name__ == "__main__":
    unittest.main()
