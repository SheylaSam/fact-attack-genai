"""The scripted user of the evaluation: answers a vote question with the gold vote, at most once. No model, no network."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_agent_graph import (CLEAR, COMPOUND, HAS_LANGGRAPH, PARTS, V1, V2, FakeEngine, FakeIndex, PresetDecider,
                                    act, search_passages, search_votes)
from tests.test_agent_session import V3, make_session

if HAS_LANGGRAPH:
    from src.agent.graph import AgentResult
    from src.agent.scripted_user import answer_for, drive
    from src.agent.session import Pending
    from src.agent.testset import EvalCase


def make_case(vote_id=V2.vote_id, *, claim='Die Mietrechtsvorlage verlangt für die Untermiete eine schriftliche '
                                           'Zustimmung.', after_answer='gestuetzt', ambiguous_with=(V1.vote_id,)):
    return EvalCase(
        id='sdev-01', split='dev', group='spezial', kind='mehrdeutig', novelty='bekannt', claim=claim,
        expected='rueckfrage', acceptable=('rueckfrage',), vote_id=vote_id, ambiguous_with=tuple(ambiguous_with),
        after_answer=after_answer, page=None, quote=None, fault=None, note='')


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class ScriptedUserTests(unittest.TestCase):
    def test_answer_for_is_the_gold_vote_when_it_is_offered(self):
        pending = Pending('Welche Vorlage?', (V1, V2), 'guard')
        self.assertEqual(answer_for(make_case(V2.vote_id), pending), V2.vote_id)
        self.assertEqual(answer_for(make_case(V1.vote_id), pending), V1.vote_id)
        # `after_answer` is the verdict after the answer, never a vote id
        self.assertEqual(answer_for(make_case(V2.vote_id, after_answer=V1.vote_id), pending), V2.vote_id)

    def test_answer_for_is_none_when_the_gold_vote_is_not_offered(self):
        pending = Pending('Welche Vorlage?', (V1, V2), 'guard')
        self.assertIsNone(answer_for(make_case(V3.vote_id), pending))
        self.assertIsNone(answer_for(make_case(None), pending))
        # the memory vote is no option: the scripted user does not use it
        self.assertIsNone(answer_for(make_case(V3.vote_id), Pending('Welche?', (V1, V2), 'guard', V3)))

    def test_scripted_user_answers_with_the_gold_vote_once(self):
        session, _, engine = make_session()
        with patch.object(session, 'answer', wraps=session.answer) as answer:
            turn = drive(session, make_case(V2.vote_id))
        answer.assert_called_once_with(V2.vote_id)
        self.assertIsInstance(turn, AgentResult)
        self.assertEqual((turn.outcome, turn.vote), ('gestuetzt', V2))
        self.assertEqual(len(engine.verify_calls), 1)
        self.assertEqual([e.args.get('antwort') for e in turn.events if e.node == 'ask'], [V2.vote_id])

    def test_scripted_user_stops_when_the_gold_vote_is_not_offered(self):
        session, _, engine = make_session()
        with patch.object(session, 'answer', wraps=session.answer) as answer:
            turn = drive(session, make_case(V3.vote_id))
        answer.assert_not_called()  # one wrong Rückfrage, no second attempt
        self.assertIsInstance(turn, Pending)
        self.assertEqual(turn.options, (V1, V2))
        self.assertEqual(turn, session.pending)
        self.assertEqual(engine.verify_calls, [])

    def test_scripted_user_never_answers_a_second_question(self):
        session, _, _ = make_session()
        second = Pending('Noch einmal?', (V1, V2), 'model')
        with patch.object(session, 'answer', return_value=second) as answer:
            turn = drive(session, make_case(V2.vote_id))
        answer.assert_called_once_with(V2.vote_id)
        self.assertIs(turn, second)

    def test_scripted_user_leaves_a_statement_question_unanswered(self):
        decider = PresetDecider(act('teilaussage_fragen', frage='Welche Teilaussage soll ich prüfen?',
                                    teilaussagen=PARTS))
        session, _, _ = make_session(decider)
        with patch.object(session, 'answer', wraps=session.answer) as answer:
            turn = drive(session, make_case(V2.vote_id, claim=COMPOUND))
        answer.assert_not_called()
        self.assertIsInstance(turn, AgentResult)
        self.assertEqual((turn.outcome, turn.asked_by, turn.statements), ('rueckfrage', 'model', PARTS))
        self.assertIsNone(session.pending)

    def test_a_check_that_never_asks_is_returned_as_it_ended(self):
        decider = PresetDecider(search_votes(), search_passages(V1), act('pruefen', vorlage_id=V1.vote_id))
        session, _, _ = make_session(decider, index=FakeIndex(CLEAR), engine=FakeEngine())
        with patch.object(session, 'answer', wraps=session.answer) as answer:
            turn = drive(session, make_case(V1.vote_id))
        answer.assert_not_called()
        self.assertEqual((turn.outcome, turn.vote), ('gestuetzt', V1))


if __name__ == '__main__':
    unittest.main()
