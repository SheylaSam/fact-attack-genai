"""AgentSession: one check kept across the question and the answer.

The fakes (index, engine, preset decider) are those of test_agent_graph.py; no network, no Apertus request.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.search_backend import LocalBackend
from src.agent.tools import AgentTools
from src.agent.trace import Tracer
from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, CLOSE, FRENCH_CLAIM, HAS_LANGGRAPH, V1, V2, FakeEngine,
                                    FakeIndex, PresetDecider, act, search_passages, search_votes, v2_engine)

if HAS_LANGGRAPH:
    from src.agent.budget import BudgetExceeded, RequestBudget
    from src.agent.graph import AgentResult
    from src.agent import session as session_module
    from src.agent.session import AgentSession, Pending


def v2_decider():
    """The guard asks back on CLOSE; after the answer V2 is searched and checked."""
    return PresetDecider(search_votes(), search_passages(V1), search_passages(V2),
                         act('pruefen', vorlage_id=V2.vote_id))


def make_session(decider=None, index=None, engine=None, tracer=None, **kwargs):
    engine = engine or v2_engine()
    tools = AgentTools(LocalBackend(index or FakeIndex(CLOSE), engine, BOOKLETS), BOOKLETS)
    session = AgentSession(tools, decider or v2_decider(), engine, tracer or Tracer(), **kwargs)
    return session, tools, engine


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class AgentSessionTests(unittest.TestCase):
    def test_start_returns_pending_for_a_close_claim_and_a_result_for_a_clear_one(self):
        session, _, _ = make_session()
        turn = session.start(CLAIM)
        self.assertIsInstance(turn, Pending)
        self.assertEqual(turn.options, (V1, V2))  # VoteRef objects, in the order of the question
        self.assertEqual(turn.asked_by, 'guard')
        self.assertIn('Welche Vorlage ist gemeint', turn.question)
        self.assertEqual(session.pending, turn)

        engine = FakeEngine()
        clear, _, _ = make_session(PresetDecider(search_votes(), search_passages(V1),
                                                 act('pruefen', vorlage_id=V1.vote_id)),
                                   index=FakeIndex(CLEAR), engine=engine)
        result = clear.start(CLAIM)
        self.assertIsInstance(result, AgentResult)
        self.assertEqual((result.outcome, result.vote), ('gestuetzt', V1))
        self.assertIsNone(clear.pending)

    def test_pending_is_a_frozen_value(self):
        session, _, _ = make_session()
        turn = session.start(CLAIM)
        with self.assertRaises(AttributeError):
            turn.question = 'anders'

    def test_answer_continues_the_same_check_and_returns_the_result(self):
        budget = RequestBudget(limit=3)
        engine = v2_engine()
        index = FakeIndex(CLOSE)
        session, tools, _ = make_session(index=index, engine=engine, budget=budget)
        pending = session.start(CLAIM)
        before = list(session.events)
        self.assertEqual(index.vote_calls, ['Untermiete', CLAIM])  # the model's search and the guard's
        result = session.answer(V2.vote_id)
        self.assertIsInstance(result, AgentResult)
        self.assertEqual((result.outcome, result.vote), ('gestuetzt', V2))
        self.assertEqual(tools.fixed_vote_id, V2.vote_id)  # the tools of the first half carry the answer
        self.assertEqual(index.vote_calls, ['Untermiete', CLAIM])  # the guard's assessment was cached, not redone
        self.assertEqual(engine.verify_calls[0]['vote'], V2.title)
        self.assertEqual(result.events[:len(before)], before)  # the events before the answer ...
        self.assertGreater(len(result.events), len(before))  # ... and the ones after it
        self.assertEqual([e.node for e in result.events if e.node == 'ask'], ['ask'])
        self.assertEqual(result.events, session.events)
        self.assertIsNone(session.pending)
        self.assertIsNone(result.question)
        self.assertEqual(pending.options, (V1, V2))

    def test_second_answer_after_the_result_is_refused_without_new_requests(self):
        session, _, engine = make_session()
        session.start(CLAIM)
        session.answer(V2.vote_id)
        calls, events = len(engine.verify_calls), len(session.events)
        with self.assertRaises(ValueError):
            session.answer(V2.vote_id)
        self.assertEqual(len(engine.verify_calls), calls)
        self.assertEqual(len(session.events), events)

    def test_answer_with_a_vote_outside_the_options_is_refused_and_the_question_stays_open(self):
        session, tools, engine = make_session()
        pending = session.start(CLAIM)
        with self.assertRaises(ValueError) as caught:
            session.answer('2024-11-24_de#7')
        self.assertIn('2024-11-24_de#7', str(caught.exception))
        self.assertEqual(session.pending, pending)
        self.assertIsNone(tools.fixed_vote_id)
        self.assertEqual(engine.verify_calls, [])
        session.answer(V1.vote_id)  # the same question can still be answered
        self.assertIsNone(session.pending)
        self.assertEqual(tools.fixed_vote_id, V1.vote_id)

    def test_answer_without_a_question_is_refused(self):
        session, _, engine = make_session()
        with self.assertRaises(ValueError):
            session.answer(V1.vote_id)
        self.assertEqual(engine.verify_calls, [])

    def test_budget_refusal_after_the_answer_is_a_technical_error(self):
        budget = RequestBudget(limit=1)
        engine = v2_engine()
        verify = engine.verify_with_passages

        def verify_and_spend(*args, **kwargs):
            budget.reserve(b'')  # the one request of the check ...
            try:
                budget.reserve(b'')  # ... and a second one, refused; swallowed like ApertusClient does
            except BudgetExceeded:
                pass
            return verify(*args, **kwargs)

        engine.verify_with_passages = verify_and_spend
        session, _, _ = make_session(engine=engine, budget=budget)
        self.assertIsInstance(session.start(CLAIM), Pending)
        self.assertFalse(budget.exceeded)  # nothing was spent before the answer
        result = session.answer(V2.vote_id)
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertIn('Anfrage-Budget erschöpft', result.error)
        self.assertEqual(result.evidence, [])
        self.assertIsNone(session.pending)

    def test_start_twice_discards_the_open_question(self):
        decider = PresetDecider(search_votes(), search_passages(V1), search_votes(), search_passages(V1),
                                search_passages(V2), act('pruefen', vorlage_id=V2.vote_id))
        tracer = Tracer()
        session, _, engine = make_session(decider, tracer=tracer)
        self.assertIsInstance(session.start(CLAIM), Pending)
        discarded = len(tracer.events)
        second = session.start(CLAIM)
        self.assertIsInstance(second, Pending)
        self.assertEqual(session.pending, second)
        self.assertEqual(session.events, tracer.events[discarded:])  # the events of the new check only
        result = session.answer(V2.vote_id)  # answers the new question, in a new thread
        self.assertEqual((result.outcome, result.vote), ('gestuetzt', V2))
        self.assertEqual(len(engine.verify_calls), 1)
        self.assertEqual(result.events, tracer.events[discarded:])
        self.assertEqual([e.node for e in result.events].count('decide'), 4)  # two before the answer, two after it
        self.assertEqual(decider.actions, [])  # nothing of the discarded run was replayed

    def test_on_event_sees_every_event_once_in_order(self):
        seen = []
        tracer = Tracer(on_event=seen.append)
        session, _, _ = make_session(tracer=tracer)
        session.start(CLAIM)
        half = len(seen)
        self.assertEqual(seen, tracer.events)
        self.assertGreater(half, 0)
        self.assertNotIn('ask', [event.node for event in seen])  # no ask event before the answer
        result = session.answer(V2.vote_id)
        self.assertEqual(seen, tracer.events)  # once each, in order, no event replayed by the resume
        self.assertEqual(seen, result.events)
        self.assertEqual([event.node for event in seen].count('ask'), 1)
        self.assertEqual(seen[-1].node, 'finish')

    def test_early_results_come_back_as_results(self):
        session, _, engine = make_session()
        result = session.start(FRENCH_CLAIM)
        self.assertIsInstance(result, AgentResult)
        self.assertEqual(result.outcome, 'quelle_fehlt')
        self.assertEqual([e.node for e in result.events], ['finish'])
        self.assertIsNone(session.pending)
        self.assertEqual(engine.verify_calls, [])

    def test_invalid_claim_raises_like_run_check(self):
        session, _, _ = make_session()
        with self.assertRaises(ValueError):
            session.start('')
        self.assertIsNone(session.pending)

    def test_an_exception_outside_the_nodes_is_a_technical_error(self):
        session, _, _ = make_session()
        with patch.object(session_module, 'prepare', side_effect=RuntimeError('kaputt')):
            result = session.start(CLAIM)
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertIn('RuntimeError: kaputt', result.error)
        self.assertIsNone(session.pending)
        self.assertEqual(result.events[-1].node, 'finish')  # the failure is in the trace too
        session, _, _ = make_session()
        session.start(CLAIM)
        with patch.object(session._graph, 'invoke', side_effect=RuntimeError('Graph kaputt')):
            result = session.answer(V2.vote_id)
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertIn('Graph kaputt', result.error)
        self.assertIsNone(session.pending)  # the answer was used up; start again for a new check

    def test_numpy_scores_of_the_real_engine_survive_the_checkpoint(self):
        """The engine's BM25 search gives numpy floats (`retrieval_score`); the paused state is serialized."""
        import numpy as np
        engine = v2_engine()
        engine.passages = [dict(p, retrieval_score=np.float64(1.5)) for p in engine.passages]
        session, _, _ = make_session(engine=engine)
        self.assertIsInstance(session.start(CLAIM), Pending)
        result = session.answer(V2.vote_id)
        self.assertEqual((result.outcome, result.error), ('gestuetzt', None))
        self.assertIs(type(engine.verify_calls[0]['passages'][0]['retrieval_score']), float)


if __name__ == '__main__':
    unittest.main()
