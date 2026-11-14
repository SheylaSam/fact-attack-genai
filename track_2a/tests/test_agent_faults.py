"""Transport-level fault injection (test cases with `fault`): timeout and http_500 under the budget transport.

No network and no Apertus request: the real ChatOpenAI client runs over `faulty_transport(httpx.MockTransport(...))`,
and the mock handler records that nothing ever reached the inner transport.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from tests.test_agent_graph import BOOKLETS, CLAIM, HAS_LANGGRAPH, FakeEngine, FakeIndex, installed

HAS_LANGCHAIN = HAS_LANGGRAPH and installed('langchain_openai')
if HAS_LANGCHAIN:
    import httpx
    from src.agent import decider as decider_module
    from src.agent.budget import RequestBudget, budget_http_client
    from src.agent.decider import JsonActionDecider, make_chat_model
    from src.agent.faults import faulty_transport
    from src.agent.graph import run_check
    from src.agent.search_backend import LocalBackend
    from src.agent.testset import FAULTS
    from src.agent.tools import AgentTools
    from src.agent.trace import Tracer

VERDICTS = ('gestuetzt', 'widersprochen', 'nicht_belegt')
SEARCH = json.dumps({'tool': 'suche_vorlagen', 'args': {'anfrage': 'Untermiete'}})
ANSWER = {'choices': [{'message': {'role': 'assistant', 'content': SEARCH}}]}


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class FaultTransportTests(unittest.TestCase):
    def setUp(self):
        self.reached_inner = []

    def inner(self):
        def handler(request):
            self.reached_inner.append(request)
            return httpx.Response(200, json=ANSWER)
        return httpx.MockTransport(handler)

    def run_faulty(self, fault, journal=None, limit=10):
        """A whole check whose LLM client is the real one, under the budget, over the faulty transport."""
        budget = RequestBudget(limit, journal_path=journal)

        def client(budget_, *args, **kwargs):
            return budget_http_client(budget_, *args, transport=faulty_transport(self.inner(), fault), **kwargs)

        with patch.object(config, 'LLM_API_KEY', 'test-key'), patch.object(decider_module, 'budget_http_client',
                                                                            client):
            chat = make_chat_model(budget)
        engine = FakeEngine()
        tools = AgentTools(LocalBackend(FakeIndex(), engine, BOOKLETS), BOOKLETS)
        result = run_check(CLAIM, tools=tools, decider=JsonActionDecider(chat), engine=engine, tracer=Tracer(),
                           budget=budget)
        return result, budget, engine

    def test_timeout_fault_makes_the_run_a_technical_error_never_a_verdict(self):
        result, budget, engine = self.run_faulty('timeout')
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertNotIn(result.outcome, VERDICTS)
        self.assertIn('Entscheider fehlgeschlagen', result.error)
        self.assertIn('Timeout', result.error)
        self.assertEqual((result.evidence, engine.verify_calls), ([], []))
        self.assertEqual(budget.used, 1)  # one request, not retried by the SDK
        self.assertEqual(self.reached_inner, [])  # nothing was sent

    def test_http_500_fault_makes_the_run_a_technical_error(self):
        result, budget, engine = self.run_faulty('http_500')
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertIn('Entscheider fehlgeschlagen', result.error)
        self.assertIn('500', result.error)
        self.assertEqual((result.evidence, engine.verify_calls), ([], []))
        self.assertEqual(budget.used, 1)
        self.assertEqual(self.reached_inner, [])

    def test_fault_transport_counts_requests_in_the_budget_journal(self):
        for fault in FAULTS:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                journal = Path(tmp) / 'journal.jsonl'
                result, budget, _ = self.run_faulty(fault, journal=journal)
                lines = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
                self.assertEqual([line['sequence'] for line in lines], [1])  # the faulty request was reserved first
                self.assertEqual(budget.used, 1)
                self.assertEqual(result.outcome, 'technischer_fehler')

    def test_every_request_fails_not_only_the_first(self):
        for fault, expected in (('timeout', httpx.TimeoutException), ('http_500', None)):
            with self.subTest(fault=fault):
                budget = RequestBudget(5)
                client = budget_http_client(budget, transport=faulty_transport(self.inner(), fault))
                for _ in range(2):
                    if expected is not None:
                        with self.assertRaises(expected):
                            client.post('http://llm.invalid/v1/chat/completions', json={})
                    else:
                        response = client.post('http://llm.invalid/v1/chat/completions', json={})
                        self.assertEqual(response.status_code, 500)
                self.assertEqual((budget.used, self.reached_inner), (2, []))

    def test_unknown_fault_is_refused_and_every_testset_fault_is_known(self):
        with self.assertRaises(ValueError):
            faulty_transport(self.inner(), 'dns_down')
        for fault in FAULTS:
            self.assertIsInstance(faulty_transport(self.inner(), fault), httpx.BaseTransport)

    def test_close_reaches_the_inner_transport(self):
        closed = []

        class Inner(httpx.BaseTransport):
            def close(self):
                closed.append(True)

        faulty_transport(Inner(), 'timeout').close()
        self.assertEqual(closed, [True])


if __name__ == '__main__':
    unittest.main()
