"""McpBackend: the SearchBackend over the own MCP server on real stdio (the fixture server of the server tests).

Every wait is bounded: a hang fails a test instead of the run.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def installed(*packages):
    try:
        return all(importlib.util.find_spec(package) is not None for package in packages)
    except (ImportError, ValueError):
        return False


HAS_MCP = installed('mcp', 'langchain_mcp_adapters')
HAS_LANGGRAPH = HAS_MCP and installed('langgraph', 'httpx')
if HAS_MCP:
    from src.agent.mcp_client import McpBackend, McpStartError, model_spec
    from src.agent.mcp_server import INJECTED_ARGS, SEARCH_TOOLS, server_environment
    from src.agent.search_backend import LocalBackend
    from src.agent.tools import ERROR_TOOL_FAILURE, AgentTools
    from tests.fixtures.mcp_fake_server import FakeEngine, FakeIndex, V1, V2
if HAS_LANGGRAPH:
    from src.agent.decider import ScriptedDecider
    from src.agent.graph import TECHNICAL_REASON, run_check, startup_failure
    from src.agent.trace import Tracer

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'mcp_fake_server.py'
BASE_DIR = FIXTURE.parents[2]
BOOKLETS = Path('/booklets')
CLAIM = 'Mieter brauchen für die Untermiete die schriftliche Zustimmung der Vermieterin.'


def fixture_command(*flags):
    return [sys.executable, str(FIXTURE), *flags]


def pid_command(pid_file, *flags):
    """The fixture server, started through a wrapper that first writes its own process id to `pid_file`."""
    code = (f"import runpy, os, sys; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
            f"sys.argv = [{str(FIXTURE)!r}, *{list(flags)!r}]; runpy.run_path({str(FIXTURE)!r}, run_name='__main__')")
    return [sys.executable, '-c', code]


def pid_alive(pid):
    return subprocess.run(['ps', '-p', str(pid)], capture_output=True).returncode == 0


@unittest.skipUnless(HAS_MCP, 'mcp and langchain-mcp-adapters are not installed (requirements-agent.txt)')
class McpBackendTests(unittest.TestCase):
    def backend(self, *flags, command=None, start=True, **options):
        options.setdefault('start_timeout', 60)
        options.setdefault('call_timeout', 20)
        backend = McpBackend(command or fixture_command(*flags), env=server_environment(), cwd=BASE_DIR, **options)
        self.addCleanup(backend.close)
        if start:
            backend.start()
        return backend

    def test_start_reads_votes_and_tool_specs(self):
        backend = self.backend()
        self.assertEqual(backend.votes, {V1.vote_id: V1, V2.vote_id: V2})
        self.assertEqual([spec['name'] for spec in backend.tool_specs()], list(SEARCH_TOOLS))

    def test_model_specs_match_the_local_specs(self):
        backend = self.backend()
        local_specs = LocalBackend(None, None, BOOKLETS).tool_specs()
        self.assertEqual(len(backend.tool_specs()), len(local_specs))
        for mcp_spec, local in zip(backend.tool_specs(), local_specs):
            with self.subTest(tool=local['name']):
                self.assertEqual((mcp_spec['name'], mcp_spec['description']), (local['name'], local['description']))
                self.assertEqual(set(mcp_spec['parameters']['properties']), set(local['parameters']['properties']))
                self.assertEqual(sorted(mcp_spec['parameters'].get('required', [])),
                                 sorted(local['parameters']['required']))

    def test_model_spec_drops_injected_arguments_and_titles(self):
        for spec in self.backend().tool_specs():
            with self.subTest(tool=spec['name']):
                self.assertEqual(set(spec), {'name', 'description', 'parameters'})
                for name in INJECTED_ARGS.get(spec['name'], ()):
                    self.assertNotIn(name, spec['parameters']['properties'])
                    self.assertNotIn(name, spec['parameters'].get('required', []))
                self.assertNotIn('title', spec['parameters'])
                for prop in spec['parameters']['properties'].values():
                    self.assertNotIn('title', prop)

    def test_round_trip_matches_the_local_backend(self):
        backend = self.backend()
        local = LocalBackend(FakeIndex(), FakeEngine(), BOOKLETS)
        self.assertEqual(backend.search_votes('Untermiete', k=5), local.search_votes('Untermiete', k=5))
        self.assertEqual(backend.proven_passages(V1.vote_id, CLAIM), local.proven_passages(V1.vote_id, CLAIM))
        self.assertEqual(backend.vector_passages(V2.vote_id, 'Eigenbedarf', k=8),
                         local.vector_passages(V2.vote_id, 'Eigenbedarf', k=8))

    def test_tool_error_becomes_a_runtime_error_with_the_german_message(self):
        backend = self.backend()
        with self.assertRaises(RuntimeError) as caught:
            backend.vector_passages('2024-11-24_de#9', 'x')
        self.assertIn('Unbekannte Vorlagen-ID', str(caught.exception))

    def test_failing_start_raises_with_the_server_stderr(self):
        backend = self.backend(command=[sys.executable, '-c', "import sys; sys.stderr.write('kaputt'); sys.exit(3)"],
                               start=False)
        started = time.monotonic()
        with self.assertRaises(McpStartError) as caught:
            backend.start()
        self.assertLess(time.monotonic() - started, 30)
        self.assertIn('MCP-Server', str(caught.exception))
        self.assertTrue(str(caught.exception).rstrip().endswith('kaputt'))

    def test_start_error_keeps_only_the_last_twenty_stderr_lines(self):
        code = "import sys\nfor i in range(60): sys.stderr.write(f'zeile {i}\\n')\nsys.exit(3)"
        backend = self.backend(command=[sys.executable, '-c', code], start=False)
        with self.assertRaises(McpStartError) as caught:
            backend.start()
        text = str(caught.exception)
        self.assertIn('zeile 59', text)
        self.assertIn('zeile 40', text)
        self.assertNotIn('zeile 39', text)

    def test_start_timeout(self):
        backend = self.backend(command=[sys.executable, '-c', 'import time; time.sleep(120)'], start=False,
                               start_timeout=1)
        started = time.monotonic()
        with self.assertRaises(McpStartError) as caught:
            backend.start()
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn('1', str(caught.exception))

    def test_start_fails_when_the_server_does_not_offer_exactly_the_search_tools(self):
        code = ("from mcp.server.fastmcp import FastMCP\n"
                "s = FastMCP('x')\n"
                "@s.tool()\n"
                "def suche_vorlagen(anfrage: str) -> dict:\n"
                "    return {}\n"
                "s.run('stdio')\n")
        backend = self.backend(command=[sys.executable, '-c', code], start=False)
        with self.assertRaises(McpStartError) as caught:
            backend.start()
        self.assertIn('suche_passagen', str(caught.exception))

    def test_calls_before_start_and_after_close_fail_without_a_server(self):
        backend = self.backend(start=False)
        with self.assertRaises(RuntimeError):
            backend.search_votes('x')
        backend.start()
        backend.close()
        with self.assertRaises(RuntimeError):
            backend.search_votes('x')

    def test_server_dying_mid_check_is_a_tool_failure_not_a_hang(self):  # Review Focus 3
        backend = self.backend(call_timeout=5)
        tools = AgentTools(backend, BOOKLETS)
        started = time.monotonic()
        obs = tools.run('suche_vorlagen', {'anfrage': 'STIRB'})
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(obs.data['error_kind'], ERROR_TOOL_FAILURE)
        again = tools.run('suche_vorlagen', {'anfrage': 'Untermiete'})  # the dead server stays a failure
        self.assertEqual(again.data['error_kind'], ERROR_TOOL_FAILURE)

    @unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
    def test_server_dying_mid_check_ends_the_run_as_technischer_fehler(self):
        backend = self.backend(call_timeout=5)
        tools = AgentTools(backend, BOOKLETS)
        started = time.monotonic()
        result = run_check('STIRB', tools=tools, decider=ScriptedDecider(), engine=object(), tracer=Tracer(),
                           claim_language='de')
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertEqual(result.reasoning, TECHNICAL_REASON)

    def test_a_call_that_hangs_ends_at_the_call_timeout(self):
        code = ("import time\nfrom mcp.server.fastmcp import FastMCP\n"
                "from src.agent.mcp_server import SEARCH_TOOLS\n"
                "s = FastMCP('x')\n"
                "def suche_vorlagen(anfrage: str) -> dict:\n    time.sleep(60)\n    return {}\n"
                "def suche_passagen(vorlage_id: str, behauptung: str) -> dict:\n    return {}\n"
                "def suche_passagen_vektor(vorlage_id: str, anfrage: str) -> dict:\n    return {}\n"
                "for f in (suche_vorlagen, suche_passagen, suche_passagen_vektor):\n    s.add_tool(f)\n"
                "@s.resource('vorlagen://katalog', mime_type='application/json')\n"
                "def katalog() -> str:\n    return '{\"votes\": []}'\n"
                "s.run('stdio')\n")
        backend = self.backend(command=[sys.executable, '-c', code], call_timeout=2)
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as caught:
            backend.search_votes('x')
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn('2', str(caught.exception))

    def test_close_is_idempotent_and_ends_the_server(self):
        pid_file = Path(tempfile.mkdtemp()) / 'pid'
        backend = self.backend(command=pid_command(pid_file))
        pid = int(pid_file.read_text())
        self.assertTrue(pid_alive(pid))
        started = time.monotonic()
        backend.close()
        backend.close()
        self.assertLess(time.monotonic() - started, 15)
        deadline = time.monotonic() + 5
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid))

    def test_close_without_start_is_harmless(self):
        self.backend(start=False).close()

    def test_failed_start_ends_the_server_process(self):
        pid_file = Path(tempfile.mkdtemp()) / 'pid'
        code = (f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)")
        backend = self.backend(command=[sys.executable, '-c', code], start=False, start_timeout=1)
        with self.assertRaises(McpStartError):
            backend.start()
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 5
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid))


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class StartupFailureTests(unittest.TestCase):
    def test_startup_failure_is_a_visible_technical_error(self):
        tracer = Tracer()
        result = startup_failure('MCP-Server nicht gestartet: kaputt', tracer)
        self.assertEqual(result.outcome, 'technischer_fehler')
        self.assertEqual(result.reasoning, TECHNICAL_REASON)
        self.assertEqual(result.error, 'MCP-Server nicht gestartet: kaputt')
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual((event.step, event.node, event.summary, event.error),
                         (0, 'start', 'Werkzeuge nicht verfügbar', 'MCP-Server nicht gestartet: kaputt'))


if __name__ == '__main__':
    unittest.main()
