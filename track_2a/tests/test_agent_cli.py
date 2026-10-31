"""Agent CLI: step list, result text, JSON output and the wiring of mock and real runs.

Index, engine, LLM client and decider factories are replaced by fakes; no test sends a network request.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.agent import cli_format
from src.agent.budget import RequestBudget
from src.agent.source_index import VoteCandidate, VoteRef
from src.agent.trace import TraceEvent
from src.inference import EvidenceSource, PredictionResult

HAS_LANGGRAPH = importlib.util.find_spec('langgraph') is not None
if HAS_LANGGRAPH:
    from src.agent import __main__ as agent_cli
    from src.agent.graph import AgentResult, EvidenceItem

V1 = VoteRef('2024-11-24_de#1', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Untermiete)', (2, 3))
V2 = VoteRef('2024-11-24_de#2', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs)', (4, 5))
CLAIM = 'Die Mietrechtsvorlage stärkt die Vermieter.'
QUOTE = 'Neu muss die Mieterin oder der Mieter die Zustimmung zur Untermiete schriftlich einholen.'
PAGE_2 = 'Untermiete\n' + QUOTE + ' Mehr.'


def event(step, node, tool=None, summary='', error=None, **fields):
    return TraceEvent(step=step, node=node, tool=tool, summary=summary, error=error, **fields)


def numbered(text):
    """The numbered step lines of a step list: {label: line}; continuation lines are indented without a number."""
    lines = [line for line in text.splitlines() if re.match(r'\s*\d+\. ', line)]
    return {line.split()[2]: line for line in lines}


class FormatStepsTests(unittest.TestCase):
    def test_format_steps_marks_question_and_error(self):
        events = [
            event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt', args={'anfrage': CLAIM}),
            event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen, beste zuerst.', args={'anfrage': CLAIM}),
            event(2, 'decide', 'suche_passagen', 'Keine gültige Aktion: Ungültige Argumente', error='Ungültige Argumente'),
            event(2, 'act', 'suche_passagen', 'Fehler: Ungültige Argumente', error='Ungültige Argumente'),
            event(3, 'decide', 'rueckfrage', 'rueckfrage gewählt'),
            event(3, 'act', 'rueckfrage', 'Rückfrage mit 2 Optionen: a, b'),
            event(3, 'finish', None, 'Ergebnis: rueckfrage, 2 Optionen'),
        ]
        text = cli_format.format_steps(events)
        rows = numbered(text)
        self.assertIn('✓', rows['suche_vorlagen'])
        self.assertIn('✗', rows['suche_passagen'])
        self.assertIn('?', rows['rueckfrage'])
        self.assertNotIn('?', rows['suche_vorlagen'])
        self.assertNotIn('✗', rows['rueckfrage'])
        self.assertIn('Ungültige Argumente', text)

    def test_format_steps_numbers_each_step_once(self):
        events = [
            event(1, 'decide', 'suche_vorlagen', 'suche_vorlagen gewählt', duration_ms=1200.0, tokens_prompt=800,
                  tokens_completion=25),
            event(1, 'act', 'suche_vorlagen', 'Gefundene Vorlagen', duration_ms=40.0),
            event(2, 'decide', 'pruefen', 'pruefen gewählt'),
            event(2, 'act', 'pruefen', 'Prüfung mit 8 Passagen aus 2024-11-24_de#1'),
            event(2, 'verify', 'pruefen', 'Label 0 (gestuetzt), 2 Zitate'),
            event(2, 'check_evidence', None, '1 gültige Belege aus 2 Zitaten: gestuetzt'),
            event(2, 'finish', None, 'Ergebnis: gestuetzt, 1 Belege'),
        ]
        text = cli_format.format_steps(events)
        numbers = [re.match(r'\s*(\d+)\. ', line).group(1) for line in text.splitlines() if re.match(r'\s*\d+\. ', line)]
        self.assertEqual(numbers, ['1', '2', '3', '4', '5'])  # decide + act are one step
        self.assertEqual([line for line in text.splitlines() if line != line.rstrip()], [])  # no trailing blanks
        self.assertIn('800+25 Tokens', text)
        self.assertIn('1.2 s', text)
        self.assertIn('Gefundene Vorlagen', text)
        self.assertIn('Prüfung mit 8 Passagen', text)

    def test_format_steps_shows_arguments_briefly_and_the_error_text(self):
        long_query = 'Untermiete ' * 40
        events = [event(1, 'decide', 'suche_passagen', 'suche_passagen gewählt',
                        args={'vorlage_id': V1.vote_id, 'anfrage': long_query}),
                  event(1, 'act', 'suche_passagen', 'Werkzeug ausgefallen', error='TimeoutError: kein Ergebnis'),
                  event(1, 'finish', None, 'Ergebnis: technischer_fehler', error='Werkzeug suche_passagen fehlgeschlagen')]
        text = cli_format.format_steps(events)
        self.assertIn(V1.vote_id, text)
        self.assertLess(max(len(line) for line in text.splitlines()), 240)  # the long query is cut
        self.assertIn('TimeoutError: kein Ergebnis', text)
        self.assertIn('Werkzeug suche_passagen fehlgeschlagen', text)

    def test_format_steps_without_events(self):
        self.assertIn('Keine Schritte', cli_format.format_steps([]))

    def test_decider_failure_without_action_is_one_failed_step(self):
        events = [event(1, 'decide', None, 'Keine Entscheidung', error='Entscheider fehlgeschlagen: Timeout'),
                  event(1, 'finish', None, 'Ergebnis: technischer_fehler', error='Entscheider fehlgeschlagen: Timeout')]
        lines = [line for line in cli_format.format_steps(events).splitlines() if re.match(r'\s*\d+\. ', line)]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all('✗' in line for line in lines))


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class FormatResultTests(unittest.TestCase):
    def test_format_result_lists_evidence_pages(self):
        result = AgentResult(outcome='gestuetzt', vote=V1, steps=3, reasoning='Die Seite nennt die Pflicht.',
                             evidence=[EvidenceItem(page=12, quote=QUOTE), EvidenceItem(page=3, quote='Zweites\nZitat')])
        text = cli_format.format_result(result)
        self.assertIn('S. 12', text)
        self.assertIn('S. 3', text)
        self.assertIn(QUOTE, text)
        self.assertIn('Zweites Zitat', text)  # line breaks of the PDF text do not break the list
        self.assertIn('gestuetzt', text)
        self.assertIn(V1.title, text)
        self.assertIn('24.11.2024', text)
        self.assertIn('Die Seite nennt die Pflicht.', text)

    def test_format_result_rueckfrage_lists_question_and_options(self):
        result = AgentResult(outcome='rueckfrage', question='Welche Vorlage ist gemeint?', options=[V1, V2],
                             reasoning='Mehrere Vorlagen passen.', steps=2)
        text = cli_format.format_result(result)
        for expected in ('rueckfrage', 'Welche Vorlage ist gemeint?', V1.vote_id, V2.vote_id, V1.title, V2.title):
            self.assertIn(expected, text)
        self.assertNotIn('S. ', text)

    def test_format_result_technical_error_and_note(self):
        failed = AgentResult(outcome='technischer_fehler', reasoning='Abgebrochen.', error='Anfrage-Budget erschöpft')
        self.assertIn('technischer_fehler', cli_format.format_result(failed))
        self.assertIn('Anfrage-Budget erschöpft', cli_format.format_result(failed))
        discarded = AgentResult(outcome='nicht_belegt', vote=V1, note='Urteil ohne prüfbaren Beleg verworfen')
        self.assertIn('Urteil ohne prüfbaren Beleg verworfen', cli_format.format_result(discarded))
        self.assertNotIn('Belege', cli_format.format_result(discarded))

    def test_result_to_dict_is_json_serializable(self):
        prediction = PredictionResult(
            claim=CLAIM, label=0, label_name='entailment', reasoning='Begründung', evidence=[QUOTE],
            evidence_sources=[EvidenceSource(quote=QUOTE, page_number=2)], strategy='agent', booklet_path='x.pdf',
            tokens_prompt=100, tokens_completion=7, tokens_total=107, latency_ms=12.0)
        result = AgentResult(outcome='gestuetzt', vote=V1, evidence=[EvidenceItem(page=2, quote=QUOTE)],
                             options=[V2], prediction=prediction, steps=2,
                             events=[event(1, 'decide', 'pruefen', 'pruefen gewählt', args={'vorlage_id': V1.vote_id})])
        data = json.loads(json.dumps(cli_format.result_to_dict(result), ensure_ascii=False))
        self.assertEqual(data['outcome'], 'gestuetzt')
        self.assertEqual(data['vote']['vote_id'], V1.vote_id)
        self.assertEqual(data['vote']['pages'], [2, 3])
        self.assertEqual(data['evidence'], [{'page': 2, 'quote': QUOTE}])
        self.assertEqual(data['options'][0]['vote_id'], V2.vote_id)
        self.assertEqual(data['prediction']['label'], 0)
        self.assertEqual(data['events'][0]['args'], {'vorlage_id': V1.vote_id})
        self.assertEqual(data['steps'], 2)
        self.assertIsNone(data['error'])
        empty = cli_format.result_to_dict(AgentResult(outcome='quelle_fehlt'))
        self.assertIsNone(empty['vote'])
        self.assertIsNone(empty['prediction'])


class FakeIndex:
    def __init__(self):
        self.votes = {vote.vote_id: vote for vote in (V1, V2)}
        self.closed = False

    def search_votes(self, query, k=5):  # two votes that are too close to call for the scripted policy
        return [VoteCandidate(V1, 0.873, 10, 2, 'Textstelle 1'), VoteCandidate(V2, 0.866, 9, 4, 'Textstelle 2')]

    def search_passages(self, vote_id, query, k=8):
        return []

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, apertus_client=None, verify_error=None):
        self.client = apertus_client
        self.verify_error = verify_error
        self.closed = False

    def retrieve_passages(self, claim, booklet_pdf, vote=None, claim_language=None, booklet_language=None, top_k=8):
        return [{'page_number': 2, 'text': PAGE_2, 'section': 'COM'}]

    def verify_with_passages(self, claim, booklet_pdf, passages, vote=None, claim_language=None, case_id=None):
        if self.verify_error:
            raise self.verify_error
        return PredictionResult(
            claim=claim, label=0, label_name='entailment', reasoning='Begründung', evidence=[QUOTE],
            evidence_sources=[EvidenceSource(quote=QUOTE, page_number=2)], strategy='agent',
            booklet_path=str(booklet_pdf), tokens_prompt=100, tokens_completion=7, tokens_total=107, latency_ms=1.0)

    def source_page_text(self, booklet_pdf, page_number):
        return PAGE_2 if page_number == 2 else None

    def close(self):
        self.closed = True


class FakeClient:
    """Stands in for ApertusClient; records how it was built."""
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.mock = kwargs.get('mock')
        self.client = None
        FakeClient.instances.append(self)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trace_dir = Path(self.tmp.name) / 'traces'
        self.index = FakeIndex()
        self.engines = []
        FakeClient.instances = []
        env = dict(os.environ)
        env.pop('FACT_ATTACK_ENV_FILE', None)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

        def make_engine(apertus_client=None, **kwargs):
            engine = FakeEngine(apertus_client, **self.engine_options)
            self.engines.append(engine)
            return engine

        self.engine_options = {}
        for name, replacement in (('SourceIndex', lambda *args, **kwargs: self.index),
                                  ('ClaimVerificationEngine', make_engine),
                                  ('ApertusClient', FakeClient),
                                  ('load_catalog', lambda path: {})):
            patcher = mock.patch.object(agent_cli, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        # a spent guard: the mock path must never build the LLM client
        guard = mock.patch('src.agent.decider.make_chat_model', side_effect=AssertionError('LLM client built'))
        guard.start()
        self.addCleanup(guard.stop)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = agent_cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_main_requires_env_without_mock(self):
        with mock.patch.object(agent_cli, 'SourceIndex', side_effect=AssertionError('index opened')):
            code, out, err = self.run_main(CLAIM, '--trace-dir', str(self.trace_dir))
        self.assertNotEqual(code, 0)
        self.assertIn('FACT_ATTACK_ENV_FILE', err)
        self.assertIn('--mock', err)
        self.assertEqual(out, '')
        self.assertFalse(self.trace_dir.exists())
        self.assertEqual(FakeClient.instances, [])

    def test_main_requires_api_key_without_mock(self):
        env_file = Path(self.tmp.name) / 'test.env'
        env_file.write_text('LLM_API_KEY=\n')
        os.environ['FACT_ATTACK_ENV_FILE'] = str(env_file)
        with mock.patch.object(config, 'LLM_API_KEY', ''), \
                mock.patch.object(agent_cli, 'SourceIndex', side_effect=AssertionError('index opened')):
            code, out, err = self.run_main(CLAIM, '--trace-dir', str(self.trace_dir))
        self.assertNotEqual(code, 0)
        self.assertIn('API', err)
        self.assertEqual(FakeClient.instances, [])

    def test_main_mock_asks_back_without_network(self):
        code, out, err = self.run_main(CLAIM, '--mock', '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 0, err)
        self.assertIn('suche_vorlagen', out)
        self.assertIn('rueckfrage', out)
        self.assertIn(V1.vote_id, out)
        self.assertIn(V2.vote_id, out)
        self.assertEqual([client.kwargs for client in FakeClient.instances], [{'mock': True}])
        self.assertTrue(self.index.closed)
        self.assertTrue(self.engines[0].closed)
        traces = list(self.trace_dir.glob('*.trace.jsonl'))
        self.assertEqual(len(traces), 1)
        self.assertEqual(json.loads(traces[0].read_text(encoding='utf-8').splitlines()[0])['node'], 'decide')
        self.assertEqual(list(self.trace_dir.glob('*.requests.jsonl')), [])

    def test_main_json_output_is_the_result_and_question_exits_zero(self):
        code, out, err = self.run_main(CLAIM, '--mock', '--json', '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 0, err)
        data = json.loads(out)  # nothing but the JSON document on stdout
        self.assertEqual(data['outcome'], 'rueckfrage')
        self.assertEqual([option['vote_id'] for option in data['options']], [V1.vote_id, V2.vote_id])
        self.assertTrue(data['events'])

    def test_main_mock_with_fixed_vote_checks_and_quotes_the_pdf(self):
        code, out, err = self.run_main(CLAIM, '--mock', '--quelle', V1.vote_id, '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 0, err)
        self.assertIn('suche_passagen', out)
        self.assertNotIn('suche_vorlagen', out)  # the vote is given
        self.assertIn('gestuetzt', out)
        self.assertIn('S. 2', out)
        self.assertIn(QUOTE, out)

    def test_main_exit_code_is_two_for_technical_error(self):
        self.engine_options = {'verify_error': RuntimeError('Modell nicht erreichbar')}
        code, out, err = self.run_main(CLAIM, '--mock', '--quelle', V1.vote_id, '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 2)
        self.assertIn('technischer_fehler', out)
        self.assertIn('Modell nicht erreichbar', out)

    def test_main_rejects_unknown_quelle(self):
        code, out, err = self.run_main(CLAIM, '--mock', '--quelle', '1999-01-01_de#9', '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 1)
        self.assertIn('1999-01-01_de#9', err)
        self.assertTrue(self.index.closed)

    def test_main_rejects_bad_claims_before_opening_the_index(self):
        with mock.patch.object(agent_cli, 'SourceIndex', side_effect=AssertionError('index opened')):
            for claim in ('', '   ', 'x' * 1001):
                code, out, err = self.run_main(claim, '--mock')
                self.assertEqual(code, 1)
                self.assertIn('1 bis 1000 Zeichen', err)

    def test_main_usage_errors_do_not_use_exit_code_two(self):
        for argv in ([CLAIM, '--max-requests', '13'], [CLAIM, '--max-requests', '0'], [CLAIM, '--modus', 'magie'], []):
            with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
                agent_cli.main(argv)
            self.assertEqual(caught.exception.code, 1, argv)

    def test_main_mock_is_always_scripted(self):
        code, out, err = self.run_main(CLAIM, '--mock', '--modus', 'json')
        self.assertEqual(code, 1)
        self.assertIn('--mock', err)
        self.assertIn('scripted', err)
        code, out, err = self.run_main(CLAIM, '--mock', '--modus', 'scripted', '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 0, err)

    def test_main_real_run_wires_the_request_cap(self):
        env_file = Path(self.tmp.name) / 'test.env'
        env_file.write_text('LLM_API_KEY=test-key\n')
        os.environ['FACT_ATTACK_ENV_FILE'] = str(env_file)
        calls = {}
        decider = object()

        def fake_install(client, budget):
            calls['install'] = (client, budget)

        def fake_make_decider(mode, budget, *, fixed_vote_id=None):
            calls['decider'] = (mode, budget, fixed_vote_id)
            return decider

        def fake_run_check(claim, *, tools, decider, engine, tracer, claim_language=None, budget=None):
            calls['run'] = {'claim': claim, 'tools': tools, 'decider': decider, 'engine': engine, 'budget': budget}
            return AgentResult(outcome='quelle_fehlt', reasoning='Keine Vorlage passt.')

        with mock.patch.object(config, 'LLM_API_KEY', 'test-key'), \
                mock.patch.object(agent_cli, 'install_on_apertus', fake_install), \
                mock.patch.object(agent_cli, 'make_decider', fake_make_decider), \
                mock.patch('src.agent.graph.run_check', fake_run_check):
            code, out, err = self.run_main(CLAIM, '--max-requests', '5', '--quelle', V2.vote_id,
                                           '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 0, err)
        self.assertEqual([client.kwargs for client in FakeClient.instances], [{'mock': False}])
        client, budget = calls['install']
        self.assertIs(client, self.engines[0].client)  # the engine's own client is counted
        self.assertIsInstance(budget, RequestBudget)
        self.assertEqual(budget.limit, 5)
        self.assertEqual(budget.journal_path.parent, self.trace_dir)
        self.assertTrue(budget.journal_path.name.endswith('.requests.jsonl'))
        self.assertEqual(calls['decider'], (agent_cli.settings.TOOL_MODE, budget, V2.vote_id))
        self.assertIs(calls['run']['budget'], budget)  # a swallowed refused request is detected
        self.assertIs(calls['run']['decider'], decider)
        self.assertIs(calls['run']['engine'], self.engines[0])
        self.assertEqual(calls['run']['tools'].fixed_vote_id, V2.vote_id)
        self.assertEqual(calls['run']['claim'], CLAIM)
        self.assertIn('Anfragen', out)
        self.assertIn('0 von 5', out)
        self.assertTrue(self.index.closed)
        self.assertTrue(self.engines[0].closed)

    def test_main_closes_index_when_the_run_raises(self):
        with mock.patch('src.agent.graph.run_check', side_effect=RuntimeError('kaputt')):
            code, out, err = self.run_main(CLAIM, '--mock', '--trace-dir', str(self.trace_dir))
        self.assertEqual(code, 1)
        self.assertIn('kaputt', err)
        self.assertTrue(self.index.closed)
        self.assertTrue(self.engines[0].closed)


if __name__ == '__main__':
    unittest.main()
