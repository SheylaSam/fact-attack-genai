"""AgentSession: one check kept across the question and the answer.

The fakes (index, engine, preset decider) are those of test_agent_graph.py; no network, no Apertus request.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.decider import ScriptedDecider
from src.agent.search_backend import LocalBackend
from src.agent.source_index import VoteRef
from src.agent.tools import AgentTools
from src.agent.trace import Tracer
from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, CLOSE, FRENCH_CLAIM, HAS_LANGGRAPH, V1, V2, FakeEngine,
                                    FakeIndex, PresetDecider, act, make_prediction, search_passages, search_votes,
                                    v2_engine)

if HAS_LANGGRAPH:
    from src.agent.budget import BudgetExceeded, RequestBudget
    from src.agent.graph import AgentResult
    from src.agent import session as session_module
    from src.agent.session import AgentSession, Pending


V3 = VoteRef('2024-11-24_de#3', '2024-11-24_de', '2024-11-24', 'de', 'Änderung des Obligationenrechts (Mietrecht: Sonstiges)',
             (4, 5))  # a vote the guard does not offer for CLAIM; the pages of v2_engine's passages
HINT = 'Zuletzt gewählte Vorlage der Person: «{title}» ({vote_id}).'


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

    def test_a_statement_question_is_a_result_and_leaves_nothing_to_answer(self):
        parts = ['Die Untermiete braucht eine schriftliche Zustimmung.', 'Die Kündigung wird leichter.']
        decider = PresetDecider(act('teilaussage_fragen', frage='Welche Teilaussage?', teilaussagen=parts))
        session, tools, engine = make_session(decider)  # CLOSE: a vote-bound action would be asked about first
        turn = session.start(CLAIM)
        self.assertIsInstance(turn, AgentResult)
        self.assertNotIsInstance(turn, Pending)
        self.assertEqual((turn.outcome, turn.asked_by, turn.statements), ('rueckfrage', 'model', parts))
        self.assertIsNone(session.pending)
        with self.assertRaises(ValueError):
            session.answer(V1.vote_id)  # a statement is no answer to this check: the app starts a new one
        self.assertIsNone(tools.fixed_vote_id)
        self.assertEqual(engine.verify_calls, [])

    def test_the_scripted_decider_finishes_the_check_with_the_answered_vote(self):
        session, _, engine = make_session(ScriptedDecider())
        self.assertIsInstance(session.start(CLAIM), Pending)
        result = session.answer(V2.vote_id)
        self.assertIsInstance(result, AgentResult)
        self.assertEqual((result.outcome, result.vote, result.error), ('gestuetzt', V2, None))
        self.assertTrue(engine.verify_calls)

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


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class LastVoteTests(unittest.TestCase):
    """The session memory: the last chosen vote is a hint and one more answer, never the choice."""

    def make(self, decider=None, **kwargs):
        return make_session(decider, index=FakeIndex(CLOSE, votes=(V1, V2, V3)), **kwargs)

    def test_hint_text_names_title_and_id(self):
        decider = v2_decider()
        session, _, _ = self.make(decider)
        session.start(CLAIM, last_vote=V2)
        content = decider.calls[0]['messages'][1]['content']
        self.assertEqual(content, CLAIM + '\n' + HINT.format(title=V2.title, vote_id=V2.vote_id))
        self.assertEqual(sum('Zuletzt gewählte Vorlage' in line for line in content.splitlines()), 1)
        self.assertNotIn('Zuletzt gewählte', decider.calls[0]['messages'][0]['content'])  # a hint, not a rule

    def test_without_a_last_vote_the_message_is_the_claim_alone(self):
        decider = v2_decider()
        session, _, _ = self.make(decider)
        session.start(CLAIM)
        self.assertEqual(decider.calls[0]['messages'][1]['content'], CLAIM)
        self.assertIsNone(session.pending.last_vote)

    def test_last_vote_is_only_a_hint_and_never_fixes_the_vote(self):
        decider = v2_decider()
        session, tools, engine = self.make(decider)
        turn = session.start(CLAIM, last_vote=V1)
        self.assertIsInstance(turn, Pending)  # the guard asks although the person's last vote is among the candidates
        self.assertEqual(turn.asked_by, 'guard')
        self.assertIsNone(tools.fixed_vote_id)
        self.assertEqual(engine.verify_calls, [])
        self.assertEqual(turn.last_vote, V1)
        self.assertEqual(turn.options, (V1, V2))
        self.assertIn('suche_vorlagen', decider.calls[0]['tools'])  # the model still searches: nothing is fixed
        result = session.answer(V2.vote_id)  # the person may pick another vote than the last one
        self.assertEqual((result.outcome, result.vote), ('gestuetzt', V2))
        self.assertEqual(tools.fixed_vote_id, V2.vote_id)

    def test_the_last_vote_outside_the_options_can_be_answered_and_nothing_else_can(self):
        decider = PresetDecider(search_votes(), search_passages(V1), search_passages(V3),
                                act('pruefen', vorlage_id=V3.vote_id))
        session, tools, engine = self.make(decider)
        turn = session.start(CLAIM, last_vote=V3)
        self.assertEqual((turn.options, turn.last_vote), ((V1, V2), V3))
        with self.assertRaises(ValueError):  # Review Focus 3: any other id stays refused
            session.answer('2024-11-24_de#9')
        self.assertEqual(session.pending, turn)
        self.assertIsNone(tools.fixed_vote_id)
        result = session.answer(V3.vote_id)
        self.assertEqual((result.outcome, result.vote), ('gestuetzt', V3))
        self.assertEqual(tools.fixed_vote_id, V3.vote_id)
        self.assertEqual(engine.verify_calls[0]['vote'], V3.title)

    def test_the_extra_answer_belongs_to_the_check_that_was_started_with_it(self):
        decider = PresetDecider(search_votes(), search_passages(V1), search_votes(), search_passages(V1))
        session, tools, _ = self.make(decider)
        session.start(CLAIM, last_vote=V3)
        second = session.start(CLAIM)  # a new check without memory
        self.assertIsNone(second.last_vote)
        with self.assertRaises(ValueError):
            session.answer(V3.vote_id)
        self.assertIsNone(tools.fixed_vote_id)

    def test_a_last_vote_the_session_does_not_know_is_ignored(self):
        stranger = VoteRef('upload-1#0', 'upload-1', '', 'de', 'Eigenes PDF', (1,))
        decider = v2_decider()
        session, _, _ = self.make(decider)
        turn = session.start(CLAIM, last_vote=stranger)
        self.assertEqual(decider.calls[0]['messages'][1]['content'], CLAIM)  # no hint for a vote nobody can fix
        self.assertIsNone(turn.last_vote)
        with self.assertRaises(ValueError):
            session.answer(stranger.vote_id)

    def test_a_given_vote_gets_no_hint_and_no_extra_answer(self):
        decider = PresetDecider(search_passages(V1), act('pruefen', vorlage_id=V1.vote_id))
        engine = v2_engine()
        tools = AgentTools(LocalBackend(FakeIndex(CLOSE, votes=(V1, V2, V3)), engine, BOOKLETS), BOOKLETS,
                           fixed_vote_id=V1.vote_id)
        session = AgentSession(tools, decider, engine, Tracer())
        session.start(CLAIM, last_vote=V2)
        self.assertEqual(decider.calls[0]['messages'][1]['content'], CLAIM)

    def test_the_scripted_decider_searches_with_the_claim_only(self):
        index = FakeIndex(CLEAR, votes=(V1, V2, V3))
        session, _, _ = make_session(ScriptedDecider(), index=index, engine=FakeEngine())
        session.start(CLAIM, last_vote=V2)
        self.assertEqual(index.vote_calls[0], CLAIM)

    def test_the_evidence_switch_reaches_the_session_and_its_answered_result(self):
        from src.agent.graph import EvidenceItem
        engine = v2_engine()
        engine.prediction = make_prediction(0, [(4, 'Dieses Zitat steht nirgends im PDF.')])
        session, _, _ = make_session(engine=engine, evidence_check=False, nachsuche=False)
        self.assertIsInstance(session.start(CLAIM), Pending)
        result = session.answer(V2.vote_id)
        self.assertEqual((result.outcome, result.evidence_validated, result.note), ('gestuetzt', False, None))
        self.assertEqual(result.evidence, [EvidenceItem(page=4, quote='Dieses Zitat steht nirgends im PDF.')])
        default, _, _ = make_session(engine=engine, nachsuche=False)
        default.start(CLAIM)
        result = default.answer(V2.vote_id)
        self.assertEqual((result.outcome, result.evidence_validated, result.note),
                         ('nicht_belegt', True, 'Urteil ohne prüfbaren Beleg verworfen'))


if __name__ == '__main__':
    unittest.main()
