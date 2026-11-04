"""The agent's own MCP server: the three search tools over real stdio, with a fake backend in a child process."""
import asyncio
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent import settings

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'mcp_fake_server.py'
TIMEOUT = 30  # seconds for every client interaction; a hang fails the test instead of the run


def installed(*packages):
    try:
        return all(importlib.util.find_spec(package) is not None for package in packages)
    except (ImportError, ValueError):
        return False


HAS_MCP = installed('mcp')
if HAS_MCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from src.agent import mcp_server
    from src.agent.mcp_server import SEARCH_TOOLS, VOTES_URI, server_environment
    from src.agent.tools import _TOOLS
    from tests.fixtures.mcp_fake_server import CANDIDATES, V1, V2

CLAIM_DE = 'Mieter brauchen für die Untermiete die schriftliche Zustimmung der Vermieterin.'
CLAIM_FR = 'Les locataires ont besoin du consentement écrit du bailleur pour la sous-location.'


class StdioServer:
    """The fixture server as a child process behind a client session kept open on a background event loop."""

    def __init__(self, *flags):
        self.flags = list(flags)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.stop_event = None
        self.session = None
        self.runner = None

    def start(self):
        self.thread.start()
        started = threading.Event()
        failure = []

        async def run():
            self.stop_event = asyncio.Event()
            params = StdioServerParameters(command=sys.executable, args=[str(FIXTURE)] + self.flags,
                                           env=server_environment(), cwd=str(FIXTURE.parents[2]))
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await asyncio.wait_for(session.initialize(), TIMEOUT)
                        self.session = session
                        started.set()
                        await self.stop_event.wait()
            except BaseException as exc:  # reported to the test thread
                failure.append(exc)
                started.set()

        self.runner = asyncio.run_coroutine_threadsafe(run(), self.loop)
        if not started.wait(TIMEOUT) or failure:
            self.stop()
            raise RuntimeError(f'MCP fixture server did not start: {failure!r}')
        return self

    def call(self, coro_factory):
        future = asyncio.run_coroutine_threadsafe(asyncio.wait_for(coro_factory(self.session), TIMEOUT), self.loop)
        return future.result(TIMEOUT + 5)

    def list_tools(self):
        return self.call(lambda s: s.list_tools()).tools

    def call_tool(self, name, arguments):
        return self.call(lambda s: s.call_tool(name, arguments))

    def read_resource(self, uri):
        return self.call(lambda s: s.read_resource(uri))

    def stop(self):
        if self.stop_event is not None:
            self.loop.call_soon_threadsafe(self.stop_event.set)
        if self.runner is not None:
            with contextlib.suppress(Exception):
                self.runner.result(TIMEOUT)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(TIMEOUT)


def payload(result):
    return json.loads(result.content[0].text)


@unittest.skipUnless(HAS_MCP, 'mcp is not installed (track_2a/requirements-agent.txt)')
class McpServerOverStdioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = StdioServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_server_lists_exactly_the_three_search_tools(self):
        tools = self.server.list_tools()
        by_name = {t.name: t for t in tools}
        self.assertEqual([t.name for t in tools], list(SEARCH_TOOLS))
        self.assertEqual({t.name: t.description for t in tools}, {n: _TOOLS[n][1] for n in SEARCH_TOOLS})
        self.assertEqual(set(by_name['suche_passagen'].inputSchema['properties']), {'vorlage_id', 'behauptung'})
        self.assertEqual(set(by_name['suche_vorlagen'].inputSchema['properties']), {'anfrage'})
        self.assertEqual(set(by_name['suche_passagen_vektor'].inputSchema['properties']), {'vorlage_id', 'anfrage'})
        model = _TOOLS['suche_passagen'][0]
        self.assertEqual(by_name['suche_passagen'].inputSchema['properties']['vorlage_id']['description'],
                         model.model_json_schema()['properties']['vorlage_id']['description'])

    def test_votes_resource_lists_every_vote(self):
        resources = self.server.call(lambda s: s.list_resources()).resources
        self.assertEqual([str(r.uri) for r in resources], [VOTES_URI])
        result = self.server.read_resource(VOTES_URI)
        self.assertEqual(result.contents[0].mimeType, 'application/json')
        votes = json.loads(result.contents[0].text)['votes']
        self.assertEqual([v['vote_id'] for v in votes], [V1.vote_id, V2.vote_id])
        self.assertEqual(votes[0]['title'], V1.title)
        self.assertEqual(votes[0]['pages'], [2, 3])

    def test_suche_vorlagen_round_trip(self):
        result = self.server.call_tool('suche_vorlagen', {'anfrage': 'Untermiete'})
        self.assertFalse(result.isError)
        expected = [{'vote_id': c.vote.vote_id, 'score': c.score, 'hits': c.hits, 'best_page': c.best_page,
                     'snippet': c.snippet} for c in CANDIDATES]
        self.assertEqual(payload(result), {'candidates': expected})

    def test_suche_passagen_gets_the_claim(self):
        result = self.server.call_tool('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': CLAIM_DE})
        self.assertFalse(result.isError)
        passages = payload(result)['passages']
        self.assertEqual(passages[0]['text'], f'Treffer zu {CLAIM_DE}')
        self.assertEqual((passages[0]['page_number'], passages[0]['section']), (2, 'COM'))
        self.assertIs(type(passages[0]['page_number']), int)  # the engine's np.int64, not the text "2"

    def test_suche_passagen_passes_the_claim_unchanged(self):
        # Like AgentTools locally: the length is checked on the stripped text, the backend gets the claim as given
        claim = f'  {CLAIM_DE} '
        result = self.server.call_tool('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': claim})
        self.assertFalse(result.isError)
        self.assertEqual(payload(result)['passages'][0]['text'], f'Treffer zu {claim}')

    def test_suche_passagen_vektor_uses_the_query(self):
        result = self.server.call_tool('suche_passagen_vektor', {'vorlage_id': V2.vote_id, 'anfrage': 'Eigenbedarf'})
        self.assertFalse(result.isError)
        passages = payload(result)['passages']
        self.assertEqual(passages[0]['text'], 'Vektortreffer zu Eigenbedarf')
        self.assertEqual(passages[0]['page_number'], 4)
        self.assertIs(type(passages[0]['page_number']), int)
        self.assertIs(type(passages[0]['retrieval_score']), float)
        self.assertEqual(passages[0]['retrieval_score'], float(np.float32(0.9)))

    def test_unknown_vote_id_is_an_error_result(self):
        for tool, args in (('suche_passagen', {'vorlage_id': '2024-11-24_de#9', 'behauptung': CLAIM_DE}),
                           ('suche_passagen_vektor', {'vorlage_id': '../../.env', 'anfrage': 'x'})):
            with self.subTest(tool=tool):
                result = self.server.call_tool(tool, args)
                self.assertTrue(result.isError)
                self.assertIn('Unbekannte Vorlagen-ID', result.content[0].text)

    def test_invalid_arguments_are_error_results(self):
        cases = (('suche_vorlagen', {'anfrage': '   '}),
                 ('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': '   '}),
                 ('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': 'x' * (settings.MAX_CLAIM_CHARS + 1)}),
                 ('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': 123}))
        for tool, args in cases:
            with self.subTest(tool=tool, args=str(args)[:50]):
                self.assertTrue(self.server.call_tool(tool, args).isError)

    def test_claim_of_maximum_length_is_accepted(self):
        claim = ((CLAIM_DE + ' ') * 20)[:settings.MAX_CLAIM_CHARS].strip()
        result = self.server.call_tool('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': claim})
        self.assertFalse(result.isError)
        padded = self.server.call_tool('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': f'  {claim}\n'})
        self.assertFalse(padded.isError)  # the length counts the stripped text

    def test_cross_lingual_claim_is_refused(self):
        result = self.server.call_tool('suche_passagen', {'vorlage_id': V1.vote_id, 'behauptung': CLAIM_FR})
        self.assertTrue(result.isError)
        self.assertIn('Übersetzung', result.content[0].text)
        self.assertIn('--werkzeuge lokal', result.content[0].text)


@unittest.skipUnless(HAS_MCP, 'mcp is not installed (track_2a/requirements-agent.txt)')
class McpServerSwitchesTests(unittest.TestCase):
    def test_stdout_noise_during_setup_does_not_break_the_protocol(self):
        server = StdioServer('--laerm').start()
        try:
            result = server.call_tool('suche_vorlagen', {'anfrage': 'Untermiete'})
            self.assertFalse(result.isError)
            self.assertEqual(len(payload(result)['candidates']), 2)
        finally:
            server.stop()

    def test_stdout_carries_nothing_but_protocol_messages(self):
        # The client library tolerates a stray line, so look at the raw stream: noise must land on stderr.
        init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 'raw-test', 'version': '0'}}}
        proc = subprocess.Popen([sys.executable, str(FIXTURE), '--laerm'], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=server_environment(),
                                cwd=str(FIXTURE.parents[2]), text=True, encoding='utf-8')
        try:
            out, err = proc.communicate(json.dumps(init) + '\n', timeout=TIMEOUT)  # EOF on stdin ends the server
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(json.loads(line)['jsonrpc'], '2.0')
        self.assertNotIn('Lärm', out)
        self.assertIn('Lärm', err)

    def test_empty_switch_returns_no_candidates(self):
        server = StdioServer('--leer').start()
        try:
            self.assertEqual(payload(server.call_tool('suche_vorlagen', {'anfrage': 'Untermiete'})),
                             {'candidates': []})
        finally:
            server.stop()


@unittest.skipUnless(HAS_MCP, 'mcp is not installed (track_2a/requirements-agent.txt)')
class McpServerHelpersTests(unittest.TestCase):
    def test_server_environment_drops_secrets(self):
        env = server_environment({'FACT_ATTACK_ENV_FILE': 'x', 'LLM_API_KEY': 'k', 'API_KEY': 'k',
                                  'BASE_URL': 'u', 'LLM_BASE_URL': 'u', 'LANGSMITH_API_KEY': 'k',
                                  'LANGSMITH_TRACING': 'true', 'HF_HOME': '/m', 'PATH': '/bin'})
        self.assertEqual(env['HF_HOME'], '/m')
        self.assertEqual(env['PATH'], '/bin')
        self.assertEqual(env['MOCK_APERTUS'], '1')
        self.assertFalse({'FACT_ATTACK_ENV_FILE', 'LLM_API_KEY', 'API_KEY', 'BASE_URL', 'LLM_BASE_URL',
                          'LANGSMITH_API_KEY', 'LANGSMITH_TRACING'} & set(env))

    def test_server_environment_takes_only_the_listed_variables(self):
        env = server_environment({'PATH': '/bin', 'SECRET_THING': 's', 'RAG_EMBEDDING_MODEL': 'm'})
        self.assertEqual(env, {'PATH': '/bin', 'RAG_EMBEDDING_MODEL': 'm', 'MOCK_APERTUS': '1',
                               'FACT_ATTACK_SKIP_DOTENV': '1'})

    def test_server_environment_switches_off_every_dotenv_file(self):
        self.assertEqual(server_environment({})['FACT_ATTACK_SKIP_DOTENV'], '1')
        self.assertEqual(server_environment({'FACT_ATTACK_SKIP_DOTENV': '0'})['FACT_ATTACK_SKIP_DOTENV'], '1')

    def test_config_in_the_server_process_loads_no_dotenv_file(self):
        # The child replaces the loader with one that fails, then imports src.config. No .env file is created:
        # the control run without the switch shows that config calls the loader otherwise.
        code = ('import sys, dotenv, dotenv.main\n'
                'def refuse(*args, **kwargs):\n'
                '    print("load_dotenv called", file=sys.stderr)\n'
                '    sys.exit(3)\n'
                'dotenv.load_dotenv = dotenv.main.load_dotenv = refuse\n'
                'import src.config\n'
                'print("config_ok")\n')
        env = server_environment()

        def run(child_env):
            return subprocess.run([sys.executable, '-c', code], cwd=str(FIXTURE.parents[2]), env=child_env,
                                  text=True, capture_output=True, timeout=TIMEOUT)

        result = run(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'config_ok')
        control = run({name: value for name, value in env.items() if name != 'FACT_ATTACK_SKIP_DOTENV'})
        self.assertEqual(control.returncode, 3, control.stderr)
        self.assertIn('load_dotenv called', control.stderr)

    def test_plain_keeps_numpy_numbers_numbers(self):
        result = mcp_server._plain({'passages': [{'page_number': np.int64(3), 'score': np.float32(0.25),
                                                  'rank': np.int32(1), 'exact': np.bool_(True),
                                                  'native': np.float64(0.5), 'path': Path('/b/x.pdf'),
                                                  'text': 'Seite'}]})
        passage = result['passages'][0]
        self.assertEqual(passage, {'page_number': 3, 'score': 0.25, 'rank': 1, 'exact': True, 'native': 0.5,
                                   'path': '/b/x.pdf', 'text': 'Seite'})
        self.assertEqual({k: type(v) for k, v in passage.items()},
                         {'page_number': int, 'score': float, 'rank': int, 'exact': bool, 'native': float,
                          'path': str, 'text': str})

    def test_server_environment_forces_the_mock_even_if_the_base_says_otherwise(self):
        self.assertEqual(server_environment({'MOCK_APERTUS': '0'})['MOCK_APERTUS'], '1')

    def test_server_command_runs_the_module_with_the_index_dir(self):
        self.assertEqual(mcp_server.server_command(Path('/idx')),
                         [sys.executable, '-m', 'src.agent.mcp_server', '--index-dir', '/idx'])

    def test_local_backend_of_the_server_never_sends(self):
        with mock.patch.object(mcp_server, 'SourceIndex') as index_class, \
                mock.patch.object(mcp_server, 'load_catalog', return_value={}):
            backend, close = mcp_server.make_local_backend(Path('/idx'))
            try:
                self.assertIs(backend.engine.client.mock, True)
                self.assertIs(backend.index, index_class.return_value)
            finally:
                close()
        index_class.return_value.close.assert_called_once_with()

    def test_main_closes_engine_and_index_when_the_server_stops(self):
        closed = []
        with mock.patch.object(mcp_server, 'make_local_backend', return_value=(object(), lambda: closed.append(1))), \
                mock.patch.object(mcp_server, 'build_server') as build:
            build.return_value.run.side_effect = RuntimeError('stop')
            self.assertEqual(mcp_server.main(['--index-dir', '/idx']), 1)
        self.assertEqual(closed, [1])


if __name__ == '__main__':
    unittest.main()
