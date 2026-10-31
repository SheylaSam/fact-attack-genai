"""RequestBudget: hard per-check cap over both HTTP paths (httpx transport and the stdlib NLI connection).

No real network: httpx.MockTransport and a fake http.client connection stand in for the paid endpoint.
"""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.agent import settings
from src.agent.budget import BudgetExceeded, RequestBudget, budget_http_client, install_on_apertus
from src.apertus_client import ApertusClient

APERTUS_MODEL = "swiss-ai/Apertus-v1.5-test"
ANSWER = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=ANSWER)


def post_once(client: httpx.Client, text: str = "hallo") -> httpx.Response:
    return client.post("http://llm.invalid/v1/chat/completions", json={"messages": [{"role": "user", "content": text}]})


class FakeResponse:
    status = 200
    headers = {}

    def read(self):
        return json.dumps(ANSWER).encode()

    def getheader(self, name, default=None):
        return default


class FakeConnection:
    """Stands in for http.client.HTTPConnection; the first `drop_first` requests fail like a closed kept-alive socket."""
    instances = []
    sent = []
    drop_first = 0

    def __init__(self, host, port=None, timeout=None):
        self.timeout, self.sock = timeout, None
        FakeConnection.instances.append(self)

    @classmethod
    def reset(cls, drop_first=0):
        cls.instances, cls.sent, cls.drop_first = [], [], drop_first

    def request(self, method, url, body=None, headers=None, **kwargs):
        if len(FakeConnection.sent) < FakeConnection.drop_first:
            FakeConnection.sent.append((method, url, body))
            raise http.client.RemoteDisconnected("server closed the kept-alive connection")
        FakeConnection.sent.append((method, url, body))

    def getresponse(self):
        return FakeResponse()

    def close(self):
        pass


class BudgetTests(unittest.TestCase):
    def test_limit_bounds(self):
        for bad in (0, -1, 13, settings.MAX_REQUESTS + 1, True, 2.0):
            with self.subTest(limit=bad), self.assertRaises(ValueError):
                RequestBudget(limit=bad)
        self.assertEqual(RequestBudget().limit, settings.MAX_REQUESTS)
        self.assertEqual(RequestBudget(limit=1).limit, 1)
        self.assertEqual(RequestBudget(limit=12).limit, 12)

    def test_third_request_raises_with_limit_two(self):
        seen = []
        budget = RequestBudget(limit=2)
        client = budget_http_client(
            budget, transport=httpx.MockTransport(lambda r: seen.append(r) or ok_handler(r)))
        self.assertEqual(post_once(client).status_code, 200)
        self.assertEqual(post_once(client).status_code, 200)
        with self.assertRaises(BudgetExceeded):
            post_once(client)
        self.assertEqual(len(seen), 2)  # the refused request never reached the inner transport
        self.assertEqual(budget.used, 2)
        with self.assertRaises(BudgetExceeded):  # and it stays refused
            post_once(client)
        self.assertEqual(len(seen), 2)

    def test_exceeded_flag_is_set_by_the_first_refusal(self):
        budget = RequestBudget(limit=1)
        budget.reserve(b"a")
        self.assertFalse(budget.exceeded)  # using the whole budget is not exceeding it
        with self.assertRaises(BudgetExceeded):
            budget.reserve(b"b")
        self.assertTrue(budget.exceeded)

    def test_budget_exceeded_is_a_runtime_error(self):
        self.assertTrue(issubclass(BudgetExceeded, RuntimeError))

    def test_redirect_hops_count_as_requests(self):
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(307, headers={"Location": "http://llm.invalid/next"})
            return httpx.Response(200, json=ANSWER)

        budget = RequestBudget(limit=5)
        client = budget_http_client(budget, transport=httpx.MockTransport(handler))
        client.follow_redirects = True
        client.post("http://llm.invalid/start", json={})
        self.assertEqual(budget.used, 2)

    def test_journal_written_before_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "check" / "budget.jsonl"
            seen = []

            def handler(request):
                seen.append([json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()])
                return ok_handler(request)

            budget = RequestBudget(limit=2, journal_path=journal)
            client = budget_http_client(budget, transport=httpx.MockTransport(handler))
            post_once(client, "eins")
            post_once(client, "zwei")
            self.assertEqual([rows[-1]["sequence"] for rows in seen], [1, 2])  # already on disk when the handler ran
            self.assertEqual(len(seen[0]), 1)
            lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["sequence"] for row in lines], [1, 2])
            for row in lines:
                self.assertEqual(set(row), {"sequence", "body_sha256", "timestamp"})
                self.assertRegex(row["body_sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(row["timestamp"])

    def test_journal_hash_is_the_request_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "budget.jsonl"
            budget = RequestBudget(limit=1, journal_path=journal)
            budget.reserve(b"payload")
            row = json.loads(journal.read_text(encoding="utf-8"))
            self.assertEqual(row["body_sha256"], hashlib.sha256(b"payload").hexdigest())

    def test_refused_request_is_not_journaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "budget.jsonl"
            budget = RequestBudget(limit=1, journal_path=journal)
            budget.reserve(b"a")
            with self.assertRaises(BudgetExceeded):
                budget.reserve(b"b")
            self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 1)

    def test_unwritable_journal_blocks_the_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("not a directory", encoding="utf-8")
            seen = []
            budget = RequestBudget(limit=2, journal_path=blocker / "budget.jsonl")
            client = budget_http_client(
                budget, transport=httpx.MockTransport(lambda r: seen.append(r) or ok_handler(r)))
            with self.assertRaises(OSError):
                post_once(client)
            self.assertEqual(seen, [])  # no journal line, no request

    def test_reserve_is_thread_safe(self):
        budget = RequestBudget(limit=12)
        outcomes = []

        def worker():
            try:
                budget.reserve(b"x")
                outcomes.append("ok")
            except BudgetExceeded:
                outcomes.append("refused")

        threads = [threading.Thread(target=worker) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(outcomes.count("ok"), 12)
        self.assertEqual(outcomes.count("refused"), 28)
        self.assertEqual(budget.used, 12)


class InstallOnApertusTests(unittest.TestCase):
    def setUp(self):
        FakeConnection.reset()
        patcher = patch.object(http.client, "HTTPConnection", FakeConnection)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = ApertusClient(base_url="http://llm.invalid/v1", api_key="test", model_name=APERTUS_MODEL, mock=False)

    def post(self):
        return self.client.client.post("/chat/completions", {"model": APERTUS_MODEL, "messages": []}, 5.0)

    def test_install_on_apertus_counts_reconnects(self):
        FakeConnection.reset(drop_first=1)  # attempt 1 fails like a dropped kept-alive socket, attempt 2 succeeds
        budget = RequestBudget(limit=5)
        install_on_apertus(self.client, budget)
        self.assertEqual(self.post()["choices"][0]["message"]["content"], "ok")
        self.assertEqual(len(FakeConnection.sent), 2)
        self.assertEqual(budget.used, 2)  # both attempts of HTTPChatClient.post are real requests

    def test_every_post_counts_and_the_cap_stops_it_before_sending(self):
        budget = RequestBudget(limit=2)
        install_on_apertus(self.client, budget)
        self.post()
        self.post()
        with self.assertRaises(BudgetExceeded):
            self.post()
        self.assertEqual(len(FakeConnection.sent), 2)
        self.assertEqual(budget.used, 2)

    def test_reconnect_that_would_exceed_the_cap_is_refused(self):
        FakeConnection.reset(drop_first=1)
        budget = RequestBudget(limit=1)
        install_on_apertus(self.client, budget)
        with self.assertRaises(BudgetExceeded):  # the resend after the dropped connection needs a second request
            self.post()
        self.assertEqual(len(FakeConnection.sent), 1)

    def test_models_listing_counts_too(self):
        # HTTPChatClient.get opens its own connection (used by _switch_model for /models), past _connection
        budget = RequestBudget(limit=1)
        install_on_apertus(self.client, budget)
        self.client.client.get("/models")
        with self.assertRaises(BudgetExceeded):
            self.client.client.get("/models")
        self.assertEqual(budget.used, 1)
        self.assertEqual([m for m, _, _ in FakeConnection.sent], ["GET"])

    def test_installing_twice_does_not_double_count(self):
        budget = RequestBudget(limit=5)
        install_on_apertus(self.client, budget)
        install_on_apertus(self.client, budget)
        self.post()
        self.assertEqual(budget.used, 1)

    def test_reinstalling_with_a_new_budget_replaces_the_old_one(self):
        old, new = RequestBudget(limit=5), RequestBudget(limit=5)
        install_on_apertus(self.client, old)
        install_on_apertus(self.client, new)
        self.post()
        self.assertEqual((old.used, new.used), (0, 1))

    def test_apertus_swallows_budget_exceeded_so_the_budget_remembers(self):
        # What ApertusClient does with the exception (no real request is sent in either case): translate() returns
        # None, infer() returns a Neutral NLIOutput whose error says "api: ..." - only `budget.exceeded` tells a
        # caller the check hit the cap and must end as a technical error.
        budget = RequestBudget(limit=1)
        install_on_apertus(self.client, budget)
        budget.reserve(b"burn")
        self.client._retry_limit = lambda error: 0  # skip the backoff sleeps of _create_with_retry
        self.assertIsNone(self.client.translate("The rent rises.", "de")[0])
        out = self.client.infer("", "Die Miete steigt.", passages=["Die Miete ist gleich geblieben."])
        self.assertEqual(out.label, 1)
        self.assertTrue(out.error.startswith("api: "))
        self.assertIn("Anfrage-Budget", out.error)
        self.assertTrue(budget.exceeded)
        self.assertEqual(FakeConnection.sent, [])

    def test_mock_client_stays_unchanged(self):
        mock_client = ApertusClient(mock=True)
        self.assertIsNone(mock_client.client)
        before = dict(vars(mock_client))
        install_on_apertus(mock_client, RequestBudget(limit=1))
        self.assertIsNone(mock_client.client)
        self.assertEqual(vars(mock_client), before)

    def test_http_chat_client_class_is_not_modified(self):
        from src.http_chat import HTTPChatClient
        original = HTTPChatClient._connection
        install_on_apertus(self.client, RequestBudget(limit=1))
        self.assertIs(HTTPChatClient._connection, original)  # wrapped on the instance only
        other = ApertusClient(base_url="http://llm.invalid/v1", api_key="test", model_name=APERTUS_MODEL, mock=False)
        self.assertNotIn("_connection", vars(other.client))
        self.assertNotIn("get", vars(other.client))


if __name__ == "__main__":
    unittest.main()
