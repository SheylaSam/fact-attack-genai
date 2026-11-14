"""The capped native tool-calling smoke (scripts/agent_native_smoke.py): refusals, rule, recorder, four requests.

No network and no key: every request goes through an httpx.MockTransport, and all files are written to temp dirs.
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.agent import settings

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'agent_native_smoke.py'


def installed(*packages):
    try:
        return all(importlib.util.find_spec(package) is not None for package in packages)
    except (ImportError, ValueError):
        return False


HAS_LANGCHAIN = installed('httpx', 'langchain_openai')
if HAS_LANGCHAIN:
    import httpx
    spec = importlib.util.spec_from_file_location('agent_native_smoke', SCRIPT)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

VOTE_B, VOTE_C = '2024-11-24_de#3', '2024-11-24_de#2'
NATIVE_ANSWERS = (('suche_vorlagen', {'anfrage': 'Mietrecht'}),
                  ('rueckfrage', {'frage': 'Welche Vorlage ist gemeint?', 'optionen': [VOTE_B, VOTE_C]}),
                  ('pruefen', {'vorlage_id': VOTE_C}))


def completion(message, finish='stop', tokens=7):
    return {'id': 'x', 'object': 'chat.completion', 'created': 1, 'model': 'm',
            'choices': [{'index': 0, 'finish_reason': finish, 'message': message}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': tokens, 'total_tokens': 100 + tokens}}


class FakeEndpoint:
    """A MockTransport handler: request 1 (no tools) answers with a JSON action, the native ones with tool calls."""

    def __init__(self, first_status=200, native=NATIVE_ANSWERS):
        self.first_status = first_status
        self.native = list(native)
        self.bodies = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        if len(self.bodies) == 1 and self.first_status != 200:
            return httpx.Response(self.first_status, json={'error': {'message': 'max_completion_tokens unbekannt'}})
        if 'tools' not in body:
            text = json.dumps({'tool': 'suche_vorlagen', 'args': {'anfrage': 'Mietrecht'}})
            return httpx.Response(200, json=completion({'role': 'assistant', 'content': text}, tokens=900))
        tool, args = self.native.pop(0)
        call = {'id': 'call_1', 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(args)}}
        return httpx.Response(200, json=completion({'role': 'assistant', 'content': None, 'tool_calls': [call]},
                                                   'tool_calls'))


def native_row(status=200, valid=True, expected=True, choice='required'):
    return {'mode': 'native', 'http_status': status, 'tool_choice': choice, 'valid_action': valid,
            'expected_tool_chosen': expected}


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class RefusalTests(unittest.TestCase):
    def run_main(self, argv, env_file='/nicht/gelesen.env', api_key='test-key'):
        """main() with the paid parts made fatal: a refusal must come before anything is built."""
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(smoke, '_env_file', return_value=env_file), \
                patch.object(config, 'LLM_API_KEY', api_key), \
                patch.object(smoke, 'RequestBudget', side_effect=AssertionError('budget built')) as budget, \
                patch.object(smoke, 'make_chat_model', side_effect=AssertionError('model built')) as model, \
                contextlib.redirect_stderr(stderr):
            self.output = Path(tmp) / 'smoke.json'
            code = smoke.main([arg.replace('OUT', str(self.output)) for arg in argv])
        budget.assert_not_called()
        model.assert_not_called()
        return code, stderr.getvalue()

    def test_refuses_without_confirmation_env_key_or_with_existing_output(self):
        code, message = self.run_main(['--output', 'OUT'])
        self.assertEqual(code, 1)
        self.assertIn('--confirm-paid', message)
        code, message = self.run_main(['--confirm-paid', '--output', 'OUT'], env_file=None)
        self.assertEqual(code, 1)
        self.assertIn('FACT_ATTACK_ENV_FILE', message)
        code, message = self.run_main(['--confirm-paid', '--output', 'OUT'], api_key='')
        self.assertEqual(code, 1)
        self.assertIn('Schlüssel', message)

    def test_refuses_when_the_result_or_the_journal_exists(self):
        for existing in ('smoke.json', 'smoke.journal.jsonl'):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / existing).write_text('alt', encoding='utf-8')
                stderr = io.StringIO()
                with patch.object(smoke, '_env_file', return_value='x'), \
                        patch.object(config, 'LLM_API_KEY', 'test-key'), \
                        patch.object(smoke, 'make_chat_model', side_effect=AssertionError('model built')), \
                        contextlib.redirect_stderr(stderr):
                    code = smoke.main(['--confirm-paid', '--output', str(Path(tmp) / 'smoke.json')])
                self.assertEqual(code, 1)
                self.assertIn('existiert schon', stderr.getvalue())
                self.assertEqual((Path(tmp) / existing).read_text(encoding='utf-8'), 'alt')


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class RuleTests(unittest.TestCase):
    def test_rule_needs_three_accepted_valid_expected_native_runs(self):
        json_row = {'mode': 'json', 'http_status': 200, 'tool_choice': None, 'valid_action': True,
                    'expected_tool_chosen': True}
        three = [native_row(), native_row(), native_row()]
        decision = smoke.decide([json_row] + three)
        self.assertEqual((decision['decision'], decision['native_accepted'], decision['native_valid'],
                          decision['native_expected_tool']), ('native', 3, 3, 3))
        self.assertEqual(decision['rule'], smoke.RULE)
        self.assertTrue(decision['rule'].startswith('native wird Standard nur, wenn alle 3 nativen Anfragen'))
        # 2 of 3 chose the expected tool: json stays
        self.assertEqual(smoke.decide([json_row, native_row(), native_row(expected=False), native_row()])['decision'],
                         'json')
        # one invalid action, one 400, one answer without tool_choice=required: json stays
        for odd in (native_row(valid=False), native_row(status=400, valid=False, expected=False),
                    native_row(choice=None)):
            with self.subTest(odd=odd):
                self.assertEqual(smoke.decide([json_row, native_row(), native_row(), odd])['decision'], 'json')
        # fewer than three native rows (stopped early) is no basis for native
        self.assertEqual(smoke.decide([json_row, native_row(), native_row()])['decision'], 'json')

    def test_completion_cap_verdict(self):
        self.assertEqual(smoke.completion_cap_verdict({'http_status': 200, 'tokens_completion': 900}), 'angenommen')
        self.assertEqual(smoke.completion_cap_verdict({'http_status': 400, 'tokens_completion': 0}), 'abgelehnt')
        self.assertEqual(smoke.completion_cap_verdict({'http_status': None, 'tokens_completion': 0,
                                                       'error': 'ReadTimeout: x'}), 'unklar')
        too_long = {'http_status': 200, 'tokens_completion': settings.LLM_MAX_TOKENS + 1}
        self.assertEqual(smoke.completion_cap_verdict(too_long), 'unklar')


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class RecorderTests(unittest.TestCase):
    def test_recorder_keeps_keys_not_values(self):
        claim = 'Geheime Behauptung über die Untermiete'
        recorder = smoke.RecordingTransport(httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        body = {'model': 'm', 'messages': [{'role': 'user', 'content': claim}], 'tool_choice': 'required',
                'max_completion_tokens': 1024, 'tools': [{'type': 'function'}]}
        with httpx.Client(transport=recorder) as client:
            client.post('http://llm.invalid/v1/chat/completions', json=body, headers={'Authorization': 'Bearer geheim'})
        self.assertEqual(recorder.records, [{'body_keys': ['max_completion_tokens', 'messages', 'model', 'tool_choice',
                                                           'tools'],
                                             'tool_choice': 'required', 'max_completion_tokens': 1024,
                                             'http_status': 200}])
        stored = json.dumps(recorder.records)
        self.assertIn('messages', stored)
        for secret in (claim, 'geheim', 'Bearer'):
            self.assertNotIn(secret, stored)

    def test_recorder_keeps_no_text_from_a_structured_tool_choice(self):
        recorder = smoke.RecordingTransport(httpx.MockTransport(lambda request: httpx.Response(400, json={})))
        body = {'tool_choice': {'type': 'function', 'function': {'name': 'Geheimtext'}}}
        with httpx.Client(transport=recorder) as client:
            client.post('http://llm.invalid/v1/chat/completions', json=body)
        self.assertNotIn('Geheimtext', json.dumps(recorder.records))
        self.assertEqual(recorder.records[0]['http_status'], 400)


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class FourRequestsTests(unittest.TestCase):
    def execute(self, endpoint):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(config, 'LLM_API_KEY', 'test-key'), \
                patch.object(config, 'LLM_BASE_URL', 'http://llm.invalid/v1'), \
                patch.object(config, 'LLM_NAME', 'test-model'), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            output = Path(tmp) / 'smoke.json'
            journal = output.with_suffix('.journal.jsonl')
            code = smoke.execute(output, journal, httpx.MockTransport(endpoint))
            report = json.loads(output.read_text(encoding='utf-8'))
            journal_lines = journal.read_text(encoding='utf-8').splitlines()
        return code, report, journal_lines

    def test_four_requests_with_mock_transport(self):
        endpoint = FakeEndpoint()
        code, report, journal_lines = self.execute(endpoint)
        self.assertEqual(code, 0)
        self.assertEqual(len(journal_lines), 4)  # exactly four requests, each journaled before it was sent
        self.assertEqual(len(endpoint.bodies), 4)
        rows = report['rows']
        self.assertEqual([(r['mode'], r['situation']) for r in rows],
                         [('json', 'a_neue_behauptung'), ('native', 'a_neue_behauptung'),
                          ('native', 'b_zwei_gleich_gute_vorlagen'), ('native', 'c_passagen_gefunden')])
        self.assertEqual([r['http_status'] for r in rows], [200] * 4)
        self.assertEqual([r['tool_choice'] for r in rows], [None, 'required', 'required', 'required'])
        self.assertEqual([r['max_completion_tokens'] for r in rows], [settings.LLM_MAX_TOKENS] * 4)
        self.assertEqual([r['tool'] for r in rows], ['suche_vorlagen', 'suche_vorlagen', 'rueckfrage', 'pruefen'])
        self.assertEqual(report['decision']['decision'], 'native')
        self.assertEqual(report['recommended_tool_mode'], 'native')
        self.assertEqual(report['completion_cap']['verdict'], 'angenommen')
        self.assertEqual((report['requests_used'], report['complete']), (4, True))
        # all six tools are offered in every native situation; the JSON request carries none natively
        self.assertNotIn('tools', endpoint.bodies[0])
        for body in endpoint.bodies[1:]:
            self.assertEqual(len(body['tools']), 6)
            self.assertEqual(body['tool_choice'], 'required')
        # the stage-3 history: suche_passagen without anfrage
        history_call = endpoint.bodies[3]['messages'][-2]['tool_calls'][0]['function']
        self.assertEqual(history_call['name'], 'suche_passagen')
        self.assertEqual(json.loads(history_call['arguments']), {'vorlage_id': smoke.SUBLET_VOTE})
        # the report keeps no request text
        self.assertNotIn(smoke.CLAIM_SUBLET, json.dumps(rows))

    def test_http_400_on_request_one_stops_the_run(self):
        endpoint = FakeEndpoint(first_status=400)
        code, report, journal_lines = self.execute(endpoint)
        self.assertEqual(code, 2)
        self.assertEqual(len(journal_lines), 1)
        self.assertEqual(len(endpoint.bodies), 1)
        rows = report['rows']
        self.assertEqual([bool(r.get('skipped')) for r in rows], [False, True, True, True])
        self.assertEqual(rows[0]['http_status'], 400)
        self.assertEqual(report['completion_cap']['verdict'], 'abgelehnt')
        self.assertEqual(report['decision']['decision'], 'json')
        self.assertFalse(report['complete'])

    def test_situations_are_the_stage_one_claims_with_stage_three_history(self):
        self.assertEqual([s.expected_tool for s in smoke.SITUATIONS], ['suche_vorlagen', 'rueckfrage', 'pruefen'])
        for situation in smoke.SITUATIONS:
            for tool, args, _ in situation.history:
                if tool == 'suche_passagen':
                    self.assertNotIn('anfrage', args)
        self.assertIn('Einschätzung: mehrdeutig', smoke.SITUATIONS[1].history[0][2].text)


if __name__ == '__main__':
    unittest.main()
