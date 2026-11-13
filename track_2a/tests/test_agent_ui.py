"""Agent view data: step rows, outcome view, card input and the live stream. Pure functions; no network, no Streamlit."""
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import agent_ui, config
from src.agent.source_index import VoteRef
from src.agent.trace import TraceEvent
from src.inference import EvidenceSource, PredictionResult

HAS_LANGGRAPH = importlib.util.find_spec('httpx') is not None and importlib.util.find_spec('langgraph') is not None
if HAS_LANGGRAPH:
    from src.agent.graph import AgentResult, EvidenceItem

V1 = VoteRef('2024-11-24_de#1', '2024-11-24_de', '2024-11-24', 'de', 'Mietrecht: Untermiete', (2, 3))
V2 = VoteRef('2024-11-24_de#2', '2024-11-24_de', '2024-11-24', 'de', 'Mietrecht: Kündigung', (4, 5))
WHOLE = VoteRef('2024-11-24_de#0', '2024-11-24_de', '2024-11-24', 'de', 'Gesamtes Büchlein', (1, 2, 3, 4, 5))
OTHER = VoteRef('2025-09-28_de#1', '2025-09-28_de', '2025-09-28', 'de', 'Eigenmietwert', (2,))


def event(step, node, tool=None, summary='', error=None, **fields):
    return TraceEvent(step=step, node=node, tool=tool, summary=summary, error=error, **fields)


def marks(rows):
    return [row.mark for row in rows]


class StepRowTests(unittest.TestCase):
    def test_a_guard_question_is_one_ask_row_and_the_blocked_action_has_no_checkmark(self):
        events = [
            event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt'),
            event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen'),
            event(2, 'decide', 'suche_passagen', 'suche_passagen gewählt'),
            event(2, 'act', 'suche_passagen', 'Wächter: mehrdeutig, a, b liegen gleichauf; suche_passagen nicht ausgeführt',
                  data={'guard': {'kind': 'mehrdeutig'}}),
            event(2, 'ask', 'rueckfrage', 'Rückfrage (Wächter) mit 2 Optionen: a, b'),
            event(2, 'finish', None, 'Ergebnis: rueckfrage, 2 Optionen'),
        ]
        rows = agent_ui.step_rows(events)
        self.assertEqual(marks(rows), ['done', 'ask'])  # the search is done, the blocked action became the question
        self.assertEqual(rows[1].title, agent_ui.TITLE_ASK)
        self.assertIn('Wächter', rows[1].detail)
        self.assertNotIn(agent_ui.TOOL_TITLES['suche_passagen'], [row.title for row in rows])

    def test_an_open_guard_question_is_already_an_ask_row_before_the_answer(self):
        events = [event(1, 'decide', 'suche_passagen', 'suche_passagen gewählt'),
                  event(1, 'act', 'suche_passagen', 'Wächter: mehrdeutig, a, b gleichauf; suche_passagen nicht ausgeführt',
                        data={'guard': {}})]
        self.assertEqual(marks(agent_ui.step_rows(events)), ['ask'])

    def test_an_answered_question_keeps_one_ask_row(self):
        events = [event(1, 'decide', 'suche_passagen'),
                  event(1, 'act', 'suche_passagen', 'Wächter: mehrdeutig; suche_passagen nicht ausgeführt', data={'guard': {}}),
                  event(1, 'ask', 'rueckfrage', 'Rückfrage beantwortet: 2024-11-24_de#1 «Untermiete»'),
                  event(2, 'decide', 'suche_passagen'), event(2, 'act', 'suche_passagen', 'Passagen gefunden')]
        rows = agent_ui.step_rows(events)
        self.assertEqual(marks(rows), ['ask', 'done'])
        self.assertIn('beantwortet', rows[0].detail)

    def test_a_model_question_is_one_ask_row(self):
        events = [
            event(1, 'decide', 'rueckfrage', 'rueckfrage gewählt'),
            event(1, 'act', 'rueckfrage', 'Rückfrage mit 2 Optionen: a, b'),
            event(1, 'ask', 'rueckfrage', 'Rückfrage (Modell) mit 2 Optionen: a, b'),
            event(1, 'finish', None, 'Ergebnis: rueckfrage, 2 Optionen'),
        ]
        rows = agent_ui.step_rows(events)
        self.assertEqual(marks(rows), ['ask'])
        self.assertEqual(rows[0].title, agent_ui.TITLE_ASK)
        self.assertIn('2 Optionen', rows[0].detail)

    def test_errors_make_the_affected_row_red_with_the_message(self):
        budget = 'Anfrage-Budget erschöpft: Mindestens eine Anfrage wurde nicht gesendet.'
        limit = 'Schrittgrenze erreicht: Nach 6 Suchschritten wurde die Suche nicht beendet (x).'
        guard = 'Wächter: Vorlagensuche zur Behauptung fehlgeschlagen: Timeout'
        cases = {
            'budget': ([event(1, 'decide', 'suche_vorlagen'), event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen'),
                        event(1, 'finish', None, 'Ergebnis: technischer_fehler', error=budget)], budget, 2),
            'limit': ([event(1, 'decide', 'suche_passagen'),
                       event(1, 'act', 'suche_passagen', 'Schrittgrenze', error=limit),
                       event(1, 'finish', None, 'Ergebnis: technischer_fehler', error=limit)], limit, 1),
            'guard search': ([event(1, 'decide', 'suche_passagen'),
                              event(1, 'act', 'suche_passagen', 'Wächter: Vorlagensuche fehlgeschlagen', error=guard),
                              event(1, 'finish', None, 'Ergebnis: technischer_fehler', error=guard)], guard, 1),
        }
        for name, (events, message, count) in cases.items():
            with self.subTest(name):
                rows = agent_ui.step_rows(events)
                self.assertEqual(len(rows), count)  # the closing error is not shown twice
                self.assertEqual(rows[-1].mark, 'error')
                self.assertEqual(rows[-1].error, message)
                self.assertEqual(marks(rows).count('error'), 1)

    def test_a_decision_that_failed_before_any_action_is_one_red_row(self):
        message = 'Entscheider fehlgeschlagen: Timeout'
        rows = agent_ui.step_rows([event(1, 'decide', None, 'Keine Entscheidung', error=message),
                                   event(1, 'finish', None, 'Ergebnis: technischer_fehler', error=message)])
        self.assertEqual([(row.mark, row.title, row.error) for row in rows],
                         [('error', agent_ui.TITLE_DECIDE, message)])

    def test_the_closing_steps_get_rows_and_the_plain_ones_do_not(self):
        events = [event(0, 'start', None, 'Start'),
                  event(2, 'decide', 'pruefen'), event(2, 'act', 'pruefen', 'Prüfung mit 8 Passagen'),
                  event(2, 'verify', 'pruefen', 'Label 1 (nicht_belegt), 0 Zitate'),
                  event(2, 'check_evidence', None, 'Nicht belegt: kein Zitat zu prüfen'),
                  event(2, 'nachsuche', None, 'Nachsuche: nicht belegt, ein weiterer Suchschritt'),
                  event(2, 'finish', None, 'Ergebnis: nicht_belegt')]
        rows = agent_ui.step_rows(events)
        self.assertEqual([row.title for row in rows],
                         [agent_ui.TOOL_TITLES['pruefen'], agent_ui.NODE_TITLES['verify'],
                          agent_ui.NODE_TITLES['check_evidence'], agent_ui.NODE_TITLES['nachsuche']])
        self.assertEqual(marks(rows), ['done'] * 4)

    def test_a_start_that_failed_is_a_red_row(self):
        rows = agent_ui.step_rows([event(0, 'start', None, 'Werkzeuge nicht verfügbar', error='kein Index')])
        self.assertEqual([(row.mark, row.error) for row in rows], [('error', 'kein Index')])

    def test_running_adds_an_open_row(self):
        events = [event(1, 'decide', 'suche_vorlagen'), event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen')]
        self.assertEqual(marks(agent_ui.step_rows(events)), ['done'])
        rows = agent_ui.step_rows(events, running=True)
        self.assertEqual(marks(rows), ['done', 'running'])
        self.assertEqual(rows[-1].title, agent_ui.TITLE_RUNNING)
        self.assertEqual(marks(agent_ui.step_rows([], running=True)), ['running'])

    def test_a_decision_without_its_action_is_the_open_row_while_running(self):
        events = [event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt')]
        rows = agent_ui.step_rows(events, running=True)
        self.assertEqual(marks(rows), ['running'])  # one open row, not two
        self.assertEqual(rows[0].title, agent_ui.TOOL_TITLES['suche_vorlagen'])

    def test_a_step_row_shows_the_summary_of_its_action(self):
        rows = agent_ui.step_rows([event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt'),
                                   event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen, beste zuerst.')])
        self.assertEqual((rows[0].title, rows[0].detail, rows[0].error),
                         (agent_ui.TOOL_TITLES['suche_vorlagen'], 'Gefundene Vorlagen, beste zuerst.', None))


@unittest.skipUnless(HAS_LANGGRAPH, 'needs requirements-agent.txt')
class OutcomeViewTests(unittest.TestCase):
    def results(self):
        return {
            'gestuetzt': AgentResult(outcome='gestuetzt', vote=V1, evidence=[EvidenceItem(2, 'Zitat')]),
            'widersprochen': AgentResult(outcome='widersprochen', vote=V1, evidence=[EvidenceItem(2, 'Zitat')]),
            'nicht_belegt': AgentResult(outcome='nicht_belegt', vote=V1),
            'rueckfrage': AgentResult(outcome='rueckfrage', question='Welche?', options=[V1, V2], asked_by='guard'),
            'quelle_fehlt': AgentResult(outcome='quelle_fehlt', reasoning='Kein Treffer'),
            'technischer_fehler': AgentResult(outcome='technischer_fehler', error='Timeout nach 30 s'),
        }

    def test_outcome_view_covers_every_outcome(self):
        expected = {'gestuetzt': ('verdict', 'ok'), 'widersprochen': ('verdict', 'warn'),
                    'nicht_belegt': ('verdict', 'info'), 'rueckfrage': ('state', 'info'),
                    'quelle_fehlt': ('state', 'warn'), 'technischer_fehler': ('state', 'error')}
        for outcome, result in self.results().items():
            with self.subTest(outcome):
                view = agent_ui.outcome_view(result)
                self.assertEqual((view.kind, view.tone), expected[outcome])
                self.assertTrue(view.title and view.text)
        titles = {agent_ui.outcome_view(result).title for result in self.results().values()}
        self.assertEqual(len(titles), 6)

    def test_a_technical_error_shows_the_error_text(self):
        view = agent_ui.outcome_view(self.results()['technischer_fehler'])
        self.assertIn('Timeout nach 30 s', view.text)
        without = agent_ui.outcome_view(AgentResult(outcome='technischer_fehler'))
        self.assertEqual(without.text, agent_ui.TEXT_TECHNICAL)

    def test_language_quelle_fehlt_asks_to_pick_the_source(self):
        language = AgentResult(outcome='quelle_fehlt', reasoning='Die Behauptung ist nicht auf Deutsch (Sprache: en).',
                               events=[event(0, 'finish', 'quelle_fehlt', 'Ergebnis: quelle_fehlt, Sprache en',
                                             args={'sprache': 'en'})])
        view = agent_ui.outcome_view(language)
        self.assertIn('Quelle selbst wählen', view.title)
        self.assertEqual(view.tone, 'warn')
        by_model = agent_ui.outcome_view(self.results()['quelle_fehlt'])
        self.assertNotEqual(by_model.title, view.title)  # a missing source found by searching is another state


@unittest.skipUnless(HAS_LANGGRAPH, 'needs requirements-agent.txt')
class CardInputTests(unittest.TestCase):
    def prediction(self, label=0):
        return PredictionResult(
            id='x', claim='Die Vorlage stärkt Mieter.', label=label, label_name=config.LABEL_MAPPING[label],
            reasoning='Engine reasoning', evidence=['[Page 2] Zitat A', '[Page 9] Zitat B'],
            evidence_sources=[EvidenceSource(quote='[Page 2] Zitat A', page_number=2),
                              EvidenceSource(quote='[Page 9] Zitat B', page_number=9)],
            strategy='hybrid', booklet_path='b.pdf', tokens_prompt=10, tokens_completion=5, tokens_total=15,
            latency_ms=12.0, p_entail=0.9)

    def test_prediction_for_card_maps_outcome_to_label_and_uses_checked_evidence(self):
        original = self.prediction(2)
        for outcome, label in (('gestuetzt', 0), ('widersprochen', 2)):
            with self.subTest(outcome):
                result = AgentResult(outcome=outcome, vote=V1, evidence=[EvidenceItem(2, 'Zitat A')],
                                     reasoning='Agent reasoning', prediction=original)
                card = agent_ui.prediction_for_card(result)
                self.assertEqual((card.label, card.label_name), (label, config.LABEL_MAPPING[label]))
                self.assertEqual([(s.page_number, s.quote) for s in card.evidence_sources], [(2, 'Zitat A')])
                self.assertEqual(card.evidence, ['Zitat A'])
                self.assertEqual(card.reasoning, 'Agent reasoning')
                self.assertEqual((card.claim, card.tokens_prompt, card.p_entail), (original.claim, 10, 0.9))
        self.assertEqual((original.label, len(original.evidence_sources)), (2, 2))  # the result's own copy is untouched

    def test_a_discarded_verdict_becomes_label_1_without_evidence(self):
        result = AgentResult(outcome='nicht_belegt', vote=V1, evidence=[], note='Urteil ohne prüfbaren Beleg verworfen',
                             reasoning='Kein Zitat steht so im PDF-Text.', prediction=self.prediction(0))
        card = agent_ui.prediction_for_card(result)
        self.assertEqual((card.label, card.label_name), (1, 'Neutral'))
        self.assertEqual((card.evidence_sources, card.evidence), ([], []))

    def test_a_result_without_a_verdict_has_no_card(self):
        for result in (AgentResult(outcome='rueckfrage', options=[V1, V2]),
                       AgentResult(outcome='technischer_fehler', error='x', prediction=self.prediction(0)),
                       AgentResult(outcome='gestuetzt', vote=V1)):  # a verdict without the engine's prediction
            with self.subTest(outcome=result.outcome):
                with self.assertRaises(ValueError):
                    agent_ui.prediction_for_card(result)


class VoteForSelectionTests(unittest.TestCase):
    votes = {vote.vote_id: vote for vote in (V1, V2, WHOLE, OTHER)}

    def test_vote_for_selection_by_title_and_whole_booklet(self):
        self.assertIs(agent_ui.vote_for_selection(self.votes, '2024-11-24_de', 'Mietrecht: Kündigung'), V2)
        self.assertIs(agent_ui.vote_for_selection(self.votes, '2024-11-24_de', 'Mietrecht: Untermiete'), V1)
        self.assertIs(agent_ui.vote_for_selection(self.votes, '2024-11-24_de', None), WHOLE)
        self.assertIs(agent_ui.vote_for_selection(self.votes, '2024-11-24_de', 'Ganzes Büchlein'), WHOLE)
        self.assertIs(agent_ui.vote_for_selection(self.votes, '2025-09-28_de', 'Eigenmietwert'), OTHER)

    def test_a_title_of_another_booklet_does_not_match(self):
        with self.assertRaises(ValueError):
            agent_ui.vote_for_selection(self.votes, '2025-09-28_de', 'Mietrecht: Untermiete')  # no whole-booklet vote

    def test_an_unknown_booklet_is_an_error(self):
        with self.assertRaises(ValueError):
            agent_ui.vote_for_selection(self.votes, '1999-01-01_de', None)


class StreamTests(unittest.TestCase):
    def test_stream_yields_events_while_the_call_runs_then_the_turn(self):
        events: 'queue.Queue[TraceEvent]' = queue.Queue()
        first_seen, finish = threading.Event(), threading.Event()
        first, second = event(1, 'decide', 'suche_vorlagen'), event(1, 'act', 'suche_vorlagen')

        def call():
            events.put(first)
            self.assertTrue(finish.wait(5), 'the consumer never saw the first event while the call ran')
            events.put(second)
            return 'turn'

        stream = agent_ui.stream(call, events)
        self.assertIs(next(stream), first)  # the call is still blocked: the event arrives before its end
        finish.set()
        self.assertEqual(list(stream), [second, 'turn'])

    def test_stream_without_events_yields_only_the_turn(self):
        self.assertEqual(list(agent_ui.stream(lambda: 'turn', queue.Queue())), ['turn'])

    def test_stream_reraises_the_calls_exception_after_the_queued_events(self):
        events: 'queue.Queue[TraceEvent]' = queue.Queue()
        item = event(1, 'decide', 'suche_vorlagen')

        def call():
            events.put(item)
            raise RuntimeError('Apertus ist nicht erreichbar')

        stream = agent_ui.stream(call, events)
        self.assertIs(next(stream), item)
        with self.assertRaisesRegex(RuntimeError, 'nicht erreichbar'):
            next(stream)

    def test_stream_leaves_no_thread_behind(self):
        before = threading.active_count()
        list(agent_ui.stream(lambda: 'turn', queue.Queue()))
        self.assertEqual(threading.active_count(), before)
        with self.assertRaises(ValueError):
            list(agent_ui.stream(lambda: (_ for _ in ()).throw(ValueError('x')), queue.Queue()))
        self.assertEqual(threading.active_count(), before)


if __name__ == '__main__':
    unittest.main()
