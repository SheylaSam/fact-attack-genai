"""Agent view data: step rows, outcome view, card input and the live stream. Pure functions; no network, no Streamlit."""
import importlib.util
from pathlib import Path
import queue
import sys
import tempfile
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
        self.assertEqual(rows[1].title, agent_ui.TITLE_QUESTION)
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
        self.assertEqual(rows[0].title, agent_ui.TITLE_QUESTION)
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


CANDIDATES = [{'vote_id': '2025-09-28_de#2', 'title': 'E-ID-Gesetz', 'date': '2025-09-28', 'score': 0.9},
              {'vote_id': '2025-09-28_de#1', 'title': 'Eigenmietwert', 'date': '2025-09-28', 'score': 0.8}]
OPTIONS = [{'vote_id': '2025-09-28_de#2', 'title': 'E-ID-Gesetz', 'date': '2025-09-28'},
           {'vote_id': '2025-09-28_de#1', 'title': 'Eigenmietwert', 'date': '2025-09-28'}]


def vote_search(candidates):
    return [event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt'),
            event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen', data={'candidates': candidates})]


class PlainStepTextTests(unittest.TestCase):
    def test_vote_search_with_two_candidates_reads_as_a_count(self):
        [row] = agent_ui.step_rows(vote_search(CANDIDATES))
        self.assertEqual(row.kind, 'votes')
        self.assertEqual(agent_ui.row_text(row, str, str), '2 passende Vorlagen gefunden')
        self.assertEqual(row.detail, 'Gefundene Vorlagen')

    def test_vote_search_with_one_or_no_candidate(self):
        [one] = agent_ui.step_rows(vote_search(CANDIDATES[:1]))
        self.assertEqual(agent_ui.row_text(one, str, str), 'Passende Vorlage gefunden: E-ID-Gesetz')
        [none] = agent_ui.step_rows(vote_search([]))
        self.assertEqual((none.kind, agent_ui.row_text(none, str, str)), ('votes', agent_ui.TITLE_NO_SOURCE))

    def test_answered_question_names_title_and_formatted_date(self):
        events = [event(1, 'decide', 'rueckfrage'),
                  event(1, 'act', 'rueckfrage', 'Wächter: mehrdeutig; nicht ausgeführt',
                        data={'guard': {'kind': 'mehrdeutig'}}),
                  event(1, 'ask', 'rueckfrage', 'Rückfrage beantwortet', args={'optionen': ['a'], 'antwort': '2025-09-28_de#2'},
                        data={'options': OPTIONS})]
        [row] = agent_ui.step_rows(events)
        self.assertEqual((row.mark, row.kind), ('ask', 'ask'))
        self.assertEqual(agent_ui.row_text(row, str, lambda d: '28. September 2025'),
                         'Du hast gewählt: E-ID-Gesetz, Abstimmung vom 28. September 2025')

    def test_an_open_question_and_a_statement_question_use_their_titles(self):
        open_q = [event(1, 'decide', 'rueckfrage'), event(1, 'act', 'rueckfrage', 'Rückfrage mit 2 Optionen')]
        [row] = agent_ui.step_rows(open_q)
        self.assertEqual((row.kind, agent_ui.row_text(row, str, str)), ('ask', agent_ui.TITLE_QUESTION))
        statement = [event(1, 'decide', 'teilaussage_fragen'), event(1, 'act', 'teilaussage_fragen', '2 Teilaussagen')]
        [row] = agent_ui.step_rows(statement)
        self.assertEqual((row.kind, agent_ui.row_text(row, str, str)), ('statement', agent_ui.TITLE_STATEMENT))

    def test_passages_and_vector_search_count_the_found_passages(self):
        events = [event(2, 'decide', 'suche_passagen'), event(2, 'act', 'suche_passagen', 'x', data={'found': 12}),
                  event(3, 'decide', 'suche_passagen_vektor'),
                  event(3, 'act', 'suche_passagen_vektor', 'y', data={'new': 3})]
        rows = agent_ui.step_rows(events)
        self.assertEqual([row.kind for row in rows], ['passages', 'vector'])
        self.assertEqual([agent_ui.row_text(row, str, str) for row in rows],
                         ['12 Textstellen im Büchlein gefunden', 'Nachsuche: 3 neue Textstellen'])

    def test_verify_and_evidence_use_the_new_data_fields(self):
        events = [event(3, 'verify', 'pruefen', 'Label 0 (gestuetzt), 5 Zitate', data={'label': 0, 'quotes': 5}),
                  event(3, 'check_evidence', None, '5 gültige Belege', data={'valid': 5, 'quotes': 5}),
                  event(4, 'verify', 'pruefen', 'Label 2', data={'label': 2, 'quotes': 2}),
                  event(4, 'check_evidence', None, '1 gültige', data={'valid': 1, 'quotes': 2}),
                  event(5, 'verify', 'pruefen', 'Label 1', data={'label': 1, 'quotes': 0}),
                  event(5, 'check_evidence', None, 'Nicht belegt', data={'valid': 0, 'quotes': 0}),
                  event(6, 'check_evidence', None, 'verworfen', data={'valid': 0, 'quotes': 3})]
        texts = [agent_ui.row_text(row, str, str) for row in agent_ui.step_rows(events)]
        self.assertEqual(texts, ['Apertus hat geprüft: gestützt', 'Alle 5 Zitate stehen wörtlich im PDF',
                                 'Apertus hat geprüft: widersprochen', '1 von 2 Zitaten stehen im PDF',
                                 'Apertus hat geprüft: nicht belegt', 'Kein Zitat zu prüfen',
                                 'Urteil verworfen: kein Zitat steht im PDF'])

    def test_one_valid_quote_and_unchecked_quotes_read_correctly(self):
        events = [event(1, 'check_evidence', None, 'a', data={'valid': 1, 'quotes': 1}),
                  event(2, 'check_evidence', None, 'b', data={'valid': 1, 'quotes': 1, 'checked': False}),
                  event(3, 'check_evidence', None, 'c', data={'valid': 3, 'quotes': 3, 'checked': False}),
                  event(4, 'check_evidence', None, 'd', data={'valid': 2, 'quotes': 4, 'checked': False})]
        texts = [agent_ui.row_text(row, str, str) for row in agent_ui.step_rows(events)]
        self.assertEqual(texts, ['Das Zitat steht wörtlich im PDF', 'Ein Zitat ungeprüft übernommen',
                                 '3 Zitate ungeprüft übernommen', '2 Zitate ungeprüft übernommen'])
        for text in ('Das Zitat steht wörtlich im PDF', 'Ein Zitat ungeprüft übernommen', '{n} Zitate ungeprüft übernommen'):
            self.assertIn(text, agent_ui.TEXT_KEYS)

    def test_events_without_data_keep_the_old_title_and_summary(self):
        events = [event(2, 'decide', 'suche_passagen'), event(2, 'act', 'suche_passagen', 'Passagen gefunden', data={})]
        [row] = agent_ui.step_rows(events)
        self.assertEqual((row.kind, row.title, row.detail), ('', 'Passagen suchen', 'Passagen gefunden'))
        self.assertEqual(agent_ui.row_text(row, str, str), 'Passagen suchen')

    def test_row_text_translates_the_outcome_word(self):
        translate = lambda s: {'Apertus hat geprüft: {v0}': 'Apertus a vérifié : {v0}', 'gestützt': 'étayée'}.get(s, s)
        [row] = agent_ui.step_rows([event(3, 'verify', 'pruefen', 's', data={'label': 0, 'quotes': 1})])
        self.assertEqual(agent_ui.row_text(row, translate, str), 'Apertus a vérifié : étayée')

    def test_new_texts_are_translatable_and_icons_cover_every_kind(self):
        for text in ('{n} passende Vorlagen gefunden', 'gestützt', 'nicht belegt', 'widersprochen'):
            self.assertIn(text, agent_ui.TEXT_KEYS)
        self.assertEqual(agent_ui.OUTCOME_WORDS, {0: 'gestützt', 1: 'nicht belegt', 2: 'widersprochen'})
        for kind in ('votes', 'ask', 'statement', 'passages', 'vector', 'verify', 'evidence', 'source_missing'):
            self.assertIn(kind, agent_ui.STEP_ICONS)

    def test_no_vote_id_or_score_reaches_a_title(self):
        events = (vote_search(CANDIDATES)
                  + [event(2, 'decide', 'suche_passagen'),
                     event(2, 'act', 'suche_passagen', 'Pool 8, Score 0.9', data={'found': 12, 'pool_size': 8}),
                     event(3, 'verify', 'pruefen', 'Label 0 (gestuetzt), 5 Zitate', data={'label': 0, 'quotes': 5}),
                     event(3, 'check_evidence', None, 'x', data={'valid': 5, 'quotes': 5})])
        for row in agent_ui.step_rows(events):
            text = agent_ui.row_text(row, str, str)
            for bad in ('_de#', 'Score', 'Pool', 'Label '):
                self.assertNotIn(bad, text)


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


def write_pdf(path, pages):
    import fitz
    with fitz.open() as document:
        for number in range(1, pages + 1):
            document.new_page().insert_text((72, 72), f'Seite {number}')
        document.save(path)


class VoteForSelectionTests(unittest.TestCase):
    votes = {vote.vote_id: vote for vote in (V1, V2, WHOLE, OTHER)}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)  # empty unless a test writes a booklet: these selections read no PDF

    def select(self, votes, source_id, title):
        return agent_ui.vote_for_selection(votes, source_id, title, booklets_dir=self.dir)

    def test_vote_for_selection_by_title_and_whole_booklet(self):
        self.assertIs(self.select(self.votes, '2024-11-24_de', 'Mietrecht: Kündigung'), V2)
        self.assertIs(self.select(self.votes, '2024-11-24_de', 'Mietrecht: Untermiete'), V1)
        self.assertIs(self.select(self.votes, '2024-11-24_de', None), WHOLE)
        self.assertIs(self.select(self.votes, '2024-11-24_de', 'Ganzes Büchlein'), WHOLE)
        self.assertIs(self.select(self.votes, '2025-09-28_de', 'Eigenmietwert'), OTHER)

    def test_a_title_of_another_booklet_does_not_match(self):
        with self.assertRaises(ValueError):
            self.select(self.votes, '2025-09-28_de', 'Mietrecht: Untermiete')  # no whole-booklet vote

    def test_an_unknown_booklet_is_an_error(self):
        write_pdf(self.dir / '1999-01-01_de.pdf', 2)  # a PDF alone makes no source known
        with self.assertRaises(ValueError):
            self.select(self.votes, '1999-01-01_de', None)

    def test_vote_for_selection_returns_a_whole_booklet_vote_for_a_source_without_a_title(self):
        from src.agent.source_index import WHOLE_BOOKLET_TITLE
        votes = {vote.vote_id: vote for vote in (V1, V2, OTHER)}  # like the real index: no "#0" for this booklet
        write_pdf(self.dir / '2024-11-24_de.pdf', 7)
        whole = self.select(votes, '2024-11-24_de', None)
        self.assertEqual(whole, VoteRef('2024-11-24_de#0', '2024-11-24_de', '2024-11-24', 'de', WHOLE_BOOKLET_TITLE,
                                        (1, 2, 3, 4, 5, 6, 7)))  # every page of the booklet, not only the votes' pages
        self.assertNotIn(whole.vote_id, votes)  # nothing is added to the index's votes
        self.assertIs(self.select(votes, '2024-11-24_de', 'Mietrecht: Kündigung'), V2)  # a chosen vote stays first
        with self.assertRaises(ValueError):  # a title that names no vote is no request for the whole booklet
            self.select(votes, '2024-11-24_de', 'Eigenmietwert')
        with self.assertRaises(ValueError):  # the booklet's PDF is missing: no vote, never an empty page list
            self.select(votes, '2025-09-28_de', None)


    def test_a_catalog_booklet_the_index_does_not_know_is_checked_as_a_whole(self):
        from src.agent.source_index import WHOLE_BOOKLET_TITLE
        catalog = {'2024-11-24_fr.pdf': {'date': '2024-11-24', 'language': 'fr',
                                         'votes': ['Sous-location', 'Besoin propre']}}
        write_pdf(self.dir / '2024-11-24_fr.pdf', 4)

        def select(source_id, title):
            return agent_ui.vote_for_selection(self.votes, source_id, title, booklets_dir=self.dir, catalog=catalog)
        whole = VoteRef('2024-11-24_fr#0', '2024-11-24_fr', '2024-11-24', 'fr', WHOLE_BOOKLET_TITLE, (1, 2, 3, 4))
        self.assertEqual(select('2024-11-24_fr', None), whole)
        self.assertEqual(select('2024-11-24_fr', 'Besoin propre'), whole)  # a vote of that booklet: checked whole
        self.assertNotIn(whole.vote_id, self.votes)
        with self.assertRaises(ValueError):  # a title that names none of the booklet's votes stays an error
            select('2024-11-24_fr', 'Mietrecht: Untermiete')
        with self.assertRaises(ValueError):  # not in the catalog: unknown, even with a PDF
            write_pdf(self.dir / '2024-11-24_it.pdf', 2)
            select('2024-11-24_it', None)
        with self.assertRaises(ValueError):  # without the catalog the index alone makes a booklet known
            self.select(self.votes, '2024-11-24_fr', None)
        (self.dir / '2024-11-24_fr.pdf').unlink()
        with self.assertRaises(ValueError):  # the booklet's PDF is missing
            select('2024-11-24_fr', None)
        self.assertIs(select('2024-11-24_de', 'Mietrecht: Untermiete'), V1)  # indexed booklets are unchanged


class WholeBookletTitleTests(unittest.TestCase):
    def test_the_placeholder_titles_are_translated_texts_of_this_module(self):
        from src.agent.source_index import WHOLE_BOOKLET_TITLE
        from src.agent.uploads import UPLOAD_TITLE
        self.assertEqual(agent_ui.TITLE_WHOLE_BOOKLET, WHOLE_BOOKLET_TITLE)
        self.assertEqual(agent_ui.TITLE_UPLOAD, UPLOAD_TITLE)
        self.assertIn(agent_ui.TITLE_WHOLE_BOOKLET, agent_ui.TEXT_KEYS)
        self.assertIn(agent_ui.TITLE_UPLOAD, agent_ui.TEXT_KEYS)


@unittest.skipUnless(HAS_LANGGRAPH, 'needs requirements-agent.txt')
class StatementQuestionTests(unittest.TestCase):
    PARTS = ['Die Untermiete braucht eine schriftliche Zustimmung.', 'Die Kündigung wegen Eigenbedarfs wird leichter.']

    def test_statement_question_is_one_ask_row_with_buttons(self):
        from tests.test_agent_graph import COMPOUND, PresetDecider, act, check, search_votes
        ask = act('teilaussage_fragen', frage='Welche Teilaussage soll ich prüfen?', teilaussagen=self.PARTS)
        result, _, _ = check(PresetDecider(search_votes(), ask), claim=COMPOUND)  # the events the graph emits
        rows = agent_ui.step_rows(result.events)
        self.assertEqual(marks(rows), ['done', 'ask'])  # the vote search, then the question as one row
        self.assertEqual(rows[1].title, agent_ui.TITLE_STATEMENT)
        self.assertIn('2 Teilaussagen', rows[1].detail)
        self.assertNotIn('Wächter', rows[1].detail)
        open_row = agent_ui.step_rows(result.events[:3], running=True)  # the decision alone: the open row
        self.assertEqual(open_row[-1].title, agent_ui.TITLE_ASK)
        self.assertEqual(agent_ui.statement_choices(result), self.PARTS)  # one button per statement, in order
        view = agent_ui.outcome_view(result)
        self.assertEqual((view.kind, view.tone, view.title, view.text),
                         ('state', 'info', agent_ui.TITLE_STATEMENT, agent_ui.TEXT_STATEMENT))
        self.assertNotIn('Vorlage', view.title + view.text)  # it does not ask for a vote
        for key in (agent_ui.TITLE_STATEMENT, agent_ui.TEXT_STATEMENT):
            self.assertIn(key, agent_ui.TEXT_KEYS)

    def test_only_a_statement_question_has_statement_choices(self):
        from src.agent.session import Pending
        vote_question = AgentResult(outcome='rueckfrage', question='Welche?', options=[V1, V2], asked_by='guard')
        for turn in (None, vote_question, AgentResult(outcome='gestuetzt', vote=V1),
                     Pending('Welche?', (V1, V2), 'guard'), RuntimeError('x')):
            with self.subTest(turn=type(turn).__name__):
                self.assertEqual(agent_ui.statement_choices(turn), [])
        self.assertEqual(agent_ui.outcome_view(vote_question).title, agent_ui.TITLE_QUESTION)


class QuestionTextTests(unittest.TestCase):
    def test_the_mismatch_question_does_not_claim_that_several_votes_fit(self):
        ambiguous = [event(2, 'ask', 'rueckfrage', 'Rückfrage', data={'kind': 'mehrdeutig'})]
        mismatch = [event(2, 'ask', 'rueckfrage', 'Rückfrage',
                          data={'kind': 'eindeutig', 'top': V1.vote_id, 'chosen': V2.vote_id})]
        model = [event(2, 'ask', 'rueckfrage', 'Rückfrage', data={'options': []})]
        stopped = [event(2, 'act', 'suche_passagen', 'Wächter: …',  # the open question: no ask event yet
                         data={'guard': {'kind': 'eindeutig', 'top': V1.vote_id, 'chosen': V2.vote_id}}),
                   event(3, 'decide', 'pruefen', 'pruefen gewählt')]
        self.assertEqual(agent_ui.question_text(stopped), agent_ui.TEXT_QUESTION_CHOOSE)
        self.assertEqual(agent_ui.question_text([]), agent_ui.TEXT_QUESTION)
        self.assertEqual(agent_ui.question_text(ambiguous), agent_ui.TEXT_QUESTION)
        self.assertEqual(agent_ui.question_text(model), agent_ui.TEXT_QUESTION)
        self.assertEqual(agent_ui.question_text(mismatch), agent_ui.TEXT_QUESTION_CHOOSE)
        self.assertNotIn('Mehrere Vorlagen', agent_ui.TEXT_QUESTION_CHOOSE)
        self.assertEqual(agent_ui.question_text(mismatch + ambiguous), agent_ui.TEXT_QUESTION)  # the latest counts


class OptionsWithLastTests(unittest.TestCase):
    def test_options_with_last_adds_one_extra_button_or_marks_the_existing_one(self):
        # a last vote outside the options: one more button, after the options
        buttons = agent_ui.options_with_last((V1, V2), OTHER)
        self.assertEqual([(button.vote, button.is_last) for button in buttons],
                         [(V1, False), (V2, False), (OTHER, True)])
        self.assertEqual([button.label for button in buttons], [V1.title, V2.title, OTHER.title])
        # a last vote that is already an option: that button carries the mark, no duplicate
        buttons = agent_ui.options_with_last((V1, V2), V2)
        self.assertEqual([(button.vote, button.is_last) for button in buttons], [(V1, False), (V2, True)])
        self.assertEqual(len({button.vote.vote_id for button in buttons}), 2)
        # no previous answer: the options as they are, none marked
        buttons = agent_ui.options_with_last((V1, V2), None)
        self.assertEqual([(button.vote, button.is_last) for button in buttons], [(V1, False), (V2, False)])

    def test_the_marked_button_is_the_last_vote_not_a_position(self):
        buttons = agent_ui.options_with_last([V2, V1], V1)  # the guard's order is kept
        self.assertEqual([button.vote for button in buttons], [V2, V1])
        self.assertEqual([button.is_last for button in buttons], [False, True])

    def test_the_label_text_is_translatable_and_names_the_vote(self):
        self.assertIn('{v0}', agent_ui.TEXT_LAST_VOTE)
        self.assertIn(agent_ui.TEXT_LAST_VOTE, agent_ui.TEXT_KEYS)


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
