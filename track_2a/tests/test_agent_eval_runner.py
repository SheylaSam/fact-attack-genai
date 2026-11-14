"""Campaign runner of the agent evaluation: total cap, journal, resume, overwrite protection and the arms B0/B1/B2.

No network and no Apertus request: fake arms, the fakes of test_agent_graph.py, the scripted decider, and for the real
LLM client an `httpx.MockTransport`. Every test set is synthetic and lives in a temporary directory.
"""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.agent import settings
from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, HAS_LANGGRAPH, QUOTE, V1, FakeEngine, FakeIndex,
                                    installed, make_prediction)

HAS_LANGCHAIN = HAS_LANGGRAPH and installed('langchain_openai')
if HAS_LANGGRAPH:
    import httpx
    from src.agent import eval_runner, graph
    from src.agent.budget import BudgetExceeded, budget_http_client
    from src.agent.eval_metrics import CaseRun
    from src.agent.faults import faulty_transport
    from src.agent.testset import EvalCase

SECRET_KEY = 'sk-geheim-0123456789abcdef'


def make_case(case_id='dev-001', *, vote_id=V1.vote_id, claim=CLAIM, group='haupt', kind=None, expected='gestuetzt',
              fault=None, split='dev'):
    return EvalCase(id=case_id, split=split, group=group, kind=kind, novelty=None if vote_id is None else 'bekannt',
                    claim=claim, expected=expected, acceptable=(expected,) if group == 'spezial' else (),
                    vote_id=vote_id, ambiguous_with=(), after_answer=None, page=None, quote=None, fault=fault,
                    note='synthetisch')


def case_run(case, outcome='gestuetzt', error=None, arm='b1'):
    return CaseRun(case=case, arm=arm, outcome=outcome, asked=False, options=(), final_vote_id=case.vote_id,
                   evidence_valid=True if outcome == 'gestuetzt' else None, tokens=10, latency_ms=5.0, tool_calls=2,
                   error=error)


def counting_arm(per_case, calls):
    """A fake arm that sends `per_case` requests on the check's budget and turns a refusal into a technical error,
    like the real arms do."""
    def build(case, budget):
        calls.append(case.id)
        try:
            for _ in range(per_case):
                budget.reserve(b'anfrage')
        except BudgetExceeded:
            return case_run(case, 'technischer_fehler', error='Anfrage-Budget erschöpft')
        return case_run(case)
    return build


def records(out_dir):
    path = Path(out_dir) / 'cases.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def journal_lines(out_dir):
    return sum(len(path.read_text(encoding='utf-8').splitlines())
               for path in (Path(out_dir) / 'journals').glob('*.jsonl'))


class B0Engine(FakeEngine):
    """FakeEngine plus the B0 entry point `verify_claim` (the existing check path)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.claim_calls = []

    def verify_claim(self, claim, booklet_pdf, claim_language=None, strategy=None, top_k=8, vote=None, case_id=None,
                     booklet_language=None):
        self.claim_calls.append({'claim': claim, 'booklet_pdf': booklet_pdf, 'vote': vote, 'case_id': case_id,
                                 'claim_language': claim_language})
        return self.prediction


def wiring(engine, *, mock=True, index=None, transport=None):
    return eval_runner.Wiring(index=index or FakeIndex(CLEAR), booklets_dir=BOOKLETS, mock=mock,
                              engine_factory=lambda: engine, transport=transport)


def chat_answer(action):
    return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(action, ensure_ascii=False)}}]}


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'lauf'
        self.cases = [make_case(f'dev-00{n}') for n in range(1, 5)]

    def test_campaign_stops_at_the_total_cap_and_marks_the_rest_not_run(self):
        calls = []
        runs = eval_runner.run_campaign('b1', self.cases, build_arm=counting_arm(5, calls), max_requests=12,
                                        out_dir=self.out)
        # dev-001: 5 of 12, dev-002: 5 of the 7 left, dev-003: cut off after the 2 left, so it is not run
        self.assertEqual(calls, ['dev-001', 'dev-002', 'dev-003'])
        self.assertEqual([run.case.id for run in runs], [case.id for case in self.cases])  # nothing dropped
        self.assertEqual([run.outcome for run in runs], ['gestuetzt', 'gestuetzt', eval_runner.NOT_RUN,
                                                         eval_runner.NOT_RUN])
        self.assertEqual([run.error for run in runs[2:]], [eval_runner.CAP_REACHED] * 2)
        self.assertEqual([record['id'] for record in records(self.out)], ['dev-001', 'dev-002'])
        self.assertEqual(journal_lines(self.out), 12)  # never more than the cap
        self.assertEqual(eval_runner.requests_spent(self.out), 12)
        not_run = json.loads((self.out / 'not_run.json').read_text(encoding='utf-8'))
        self.assertEqual([item['id'] for item in not_run], ['dev-003', 'dev-004'])

    def test_each_check_gets_at_most_the_per_check_cap(self):
        limits = []

        def build(case, budget):
            limits.append(budget.limit)
            return case_run(case)
        eval_runner.run_campaign('b1', self.cases[:2], build_arm=build, max_requests=100, out_dir=self.out)
        self.assertEqual(limits, [settings.MAX_REQUESTS] * 2)

    def test_resume_skips_finished_cases_and_counts_their_requests(self):
        first = eval_runner.run_campaign('b1', self.cases, build_arm=counting_arm(5, []), max_requests=12,
                                         out_dir=self.out)
        calls = []
        runs = eval_runner.run_campaign('b1', self.cases, build_arm=counting_arm(5, calls), max_requests=22,
                                        out_dir=self.out, resume=True)
        # 12 spent before (5 + 5 + the 2 of the cut-off dev-003): 10 left for dev-003 and dev-004
        self.assertEqual(calls, ['dev-003', 'dev-004'])
        self.assertEqual(runs[:2], first[:2])  # the finished cases come back from cases.jsonl
        self.assertEqual([run.outcome for run in runs], ['gestuetzt'] * 4)
        self.assertEqual([record['id'] for record in records(self.out)], [case.id for case in self.cases])
        self.assertEqual(eval_runner.requests_spent(self.out), 22)
        self.assertEqual(journal_lines(self.out), 22)

        calls.clear()  # everything finished: a further resume runs nothing and returns the same runs
        again = eval_runner.run_campaign('b1', self.cases, build_arm=counting_arm(5, calls), max_requests=22,
                                         out_dir=self.out, resume=True)
        self.assertEqual((calls, again), ([], runs))
        with self.assertRaises(ValueError):  # another case list is another campaign
            eval_runner.run_campaign('b1', self.cases + [make_case('dev-005')], build_arm=counting_arm(5, calls),
                                     max_requests=40, out_dir=self.out, resume=True)
        self.assertEqual(calls, [])

    def test_resume_counts_recorded_requests_without_journals_and_refuses_another_arm(self):
        eval_runner.run_campaign('b1', self.cases[:2], build_arm=counting_arm(5, []), max_requests=24,
                                 out_dir=self.out)
        for path in (self.out / 'journals').glob('*.jsonl'):
            path.unlink()
        self.assertEqual(eval_runner.requests_spent(self.out), 10)  # from the records' "requests"
        with self.assertRaises(ValueError):
            eval_runner.run_campaign('b2', self.cases[:2], build_arm=counting_arm(5, []), max_requests=24,
                                     out_dir=self.out, resume=True)

    def test_existing_out_dir_without_resume_is_refused(self):
        self.out.mkdir()
        (self.out / 'cases.jsonl').write_text('{"id": "alt"}\n', encoding='utf-8')
        calls = []
        with self.assertRaises(FileExistsError):
            eval_runner.run_campaign('b1', self.cases, build_arm=counting_arm(1, calls), max_requests=10,
                                     out_dir=self.out)
        self.assertEqual(calls, [])
        self.assertEqual((self.out / 'cases.jsonl').read_text(encoding='utf-8'), '{"id": "alt"}\n')

    def test_unknown_arm_and_duplicate_ids_are_refused(self):
        with self.assertRaises(ValueError):
            eval_runner.run_campaign('b9', self.cases, build_arm=counting_arm(1, []), max_requests=10,
                                     out_dir=self.out)
        with self.assertRaises(ValueError):
            eval_runner.run_campaign('b1', [self.cases[0], self.cases[0]], build_arm=counting_arm(1, []),
                                     max_requests=10, out_dir=self.out)
        self.assertFalse(self.out.exists())

    def test_journal_is_written_before_each_request(self):
        seen = []

        def build(case, budget):
            def handler(request):  # the request "goes out": its journal line must already be on disk
                seen.append(len(budget.journal_path.read_text(encoding='utf-8').splitlines()))
                return httpx.Response(200, json={})
            client = budget_http_client(budget, transport=httpx.MockTransport(handler))
            for _ in range(3):
                client.post('http://llm.invalid/v1/chat/completions', json={'n': len(seen)})
            return case_run(case)

        with patch.object(eval_runner.os, 'fsync', wraps=os.fsync) as fsync:
            eval_runner.run_campaign('b1', self.cases[:2], build_arm=build, max_requests=30, out_dir=self.out)
        self.assertEqual(seen, [1, 2, 3, 1, 2, 3])
        self.assertGreaterEqual(fsync.call_count, 2)  # cases.jsonl is synced after each case
        lines = [json.loads(line) for line in (self.out / 'journals' / 'dev-001.requests.jsonl')
                 .read_text(encoding='utf-8').splitlines()]
        self.assertEqual([line['sequence'] for line in lines], [1, 2, 3])
        self.assertEqual(records(self.out)[0]['requests'], 3)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class ArmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'lauf'

    def run_arm(self, arm, cases, engine, **kwargs):
        build = eval_runner.make_arm(arm, wiring(engine, **kwargs))
        return eval_runner.run_campaign(arm, cases, build_arm=build, max_requests=12 * len(cases),
                                        out_dir=self.out / arm)

    def test_b0_uses_the_oracle_booklet_and_no_vote(self):
        engine = B0Engine(prediction=make_prediction(0, [(2, QUOTE)]))
        index = FakeIndex(CLEAR)
        with patch.object(eval_runner, 'valid_evidence', wraps=graph.valid_evidence) as check:
            [run] = self.run_arm('b0', [make_case(vote_id=V1.vote_id)], engine, index=index)
        [call] = engine.claim_calls
        self.assertEqual(call['booklet_pdf'], BOOKLETS / f'{V1.source_id}.pdf')  # the gold vote's booklet
        self.assertIsNone(call['vote'])  # "Automatisch": no vote is given
        self.assertEqual((engine.retrieve_calls, engine.verify_calls, index.vote_calls), ([], [], []))
        self.assertEqual((run.arm, run.outcome, run.final_vote_id, run.asked, run.tool_calls),
                         ('b0', 'gestuetzt', V1.vote_id, False, 0))
        self.assertTrue(run.evidence_valid)
        self.assertEqual(run.tokens, 107)
        check.assert_called_once()  # the graph's own evidence check, after the run

        made_up = B0Engine(prediction=make_prediction(2, [(2, 'Dieses Zitat steht so nirgends im Büchlein.')]))
        self.out = self.out / 'zwei'
        [invented] = self.run_arm('b0', [make_case(vote_id=V1.vote_id)], made_up)
        self.assertEqual(invented.outcome, 'widersprochen')  # the outcome is never changed by the post-hoc check
        self.assertFalse(invented.evidence_valid)

    def test_b0_has_no_result_for_ohne_abstimmung_cases(self):
        engine = B0Engine()
        case = make_case('sdev-07', vote_id=None, group='spezial', kind='ohne_abstimmung', expected='quelle_fehlt')
        [run] = self.run_arm('b0', [case], engine)
        self.assertEqual(engine.claim_calls, [])
        self.assertEqual((run.outcome, run.error, run.evidence_valid, run.tokens), (eval_runner.NOT_SCORED, None,
                                                                                   None, 0))
        self.assertEqual(records(self.out / 'b0')[0]['outcome'], eval_runner.NOT_SCORED)
        self.assertEqual(eval_runner.scored([run]), [])  # left out of every metric

    def test_b2_runs_without_nachsuche_and_without_evidence_check(self):
        invented = make_prediction(0, [(2, 'Dieses Zitat steht so nirgends im Büchlein und ist erfunden.')])
        sessions = []
        real = eval_runner.AgentSession

        def recording(*args, **kwargs):
            sessions.append(kwargs)
            return real(*args, **kwargs)

        with patch.object(eval_runner, 'AgentSession', side_effect=recording):
            [b1] = self.run_arm('b1', [make_case()], FakeEngine(prediction=invented))
            [b2] = self.run_arm('b2', [make_case()], FakeEngine(prediction=invented))
        self.assertEqual([(kw['nachsuche'], kw['evidence_check']) for kw in sessions], [(True, True), (False, False)])
        self.assertEqual(b1.outcome, 'nicht_belegt')  # B1 discards the verdict without a valid quote
        self.assertEqual(b2.outcome, 'gestuetzt')  # B2 takes the quote over unchecked
        self.assertFalse(b2.evidence_valid)  # the same check after the run, for the metric only
        self.assertEqual((b1.final_vote_id, b2.final_vote_id), (V1.vote_id, V1.vote_id))
        self.assertGreater(b2.tool_calls, 0)

    @unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
    def test_fault_case_gets_the_transport_fault_and_ends_technical(self):
        reached = []
        actions = [{'tool': 'suche_vorlagen', 'args': {'anfrage': 'Untermiete'}},
                   {'tool': 'suche_passagen', 'args': {'vorlage_id': V1.vote_id}},
                   {'tool': 'pruefen', 'args': {'vorlage_id': V1.vote_id}}]

        def handler(request):
            reached.append(request)
            return httpx.Response(200, json=chat_answer(actions[(len(reached) - 1) % 3]))

        fault_case = make_case('sdev-09', group='spezial', kind='api_ausfall', expected='technischer_fehler',
                               fault='timeout')
        cases = [fault_case, make_case('dev-001')]
        with patch.object(config, 'LLM_API_KEY', 'test-key'), \
                patch.object(eval_runner, 'faulty_transport', wraps=faulty_transport) as injected:
            fault_run, normal_run = self.run_arm('b1', cases, FakeEngine(), mock=False,
                                                 transport=lambda: httpx.MockTransport(handler))
        injected.assert_called_once()
        self.assertEqual(injected.call_args.args[1], 'timeout')
        self.assertEqual(fault_run.outcome, 'technischer_fehler')
        self.assertIn('Timeout', fault_run.error)
        self.assertEqual(len(reached), 3)  # only the case without a fault reached the transport
        self.assertEqual((normal_run.outcome, normal_run.error), ('gestuetzt', None))
        [fault_record, normal_record] = records(self.out / 'b1')
        self.assertEqual((fault_record['requests'], normal_record['requests']), (1, 3))

    def test_fault_case_without_any_request_is_marked_not_triggered(self):
        fault_case = make_case('sdev-09', group='spezial', kind='api_ausfall', expected='technischer_fehler',
                               fault='http_500')
        [run] = self.run_arm('b1', [fault_case], FakeEngine())  # mock: the scripted decider sends nothing
        self.assertEqual(run.outcome, 'gestuetzt')  # recorded as it came out, not disguised
        self.assertEqual(run.error, eval_runner.FAULT_NOT_TRIGGERED)

    def test_technical_error_always_has_a_reason(self):
        broken = FakeEngine(verify_error=RuntimeError('kaputt'))
        [run] = self.run_arm('b1', [make_case()], broken)
        self.assertEqual(run.outcome, 'technischer_fehler')
        self.assertTrue(run.error)
        self.assertIsNone(run.evidence_valid)


def write_synthetic_testset(root, cases):
    """cases.jsonl and manifest.json of a synthetic locked set in `root`."""
    from dataclasses import asdict
    import hashlib
    root.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({**asdict(case), 'claim_language': 'de'}, ensure_ascii=False) for case in cases]
    data = ('\n'.join(lines) + '\n').encode('utf-8')
    (root / 'cases.jsonl').write_bytes(data)
    counts = {}
    for case in cases:
        counts[f'{case.split}/{case.group}'] = counts.get(f'{case.split}/{case.group}', 0) + 1
    (root / 'manifest.json').write_text(json.dumps({'cases_sha256': hashlib.sha256(data).hexdigest(),
                                                    'counts': {'split_group': counts}}), encoding='utf-8')
    return root / 'cases.jsonl', root / 'manifest.json'


@unittest.skipUnless(HAS_LANGCHAIN, 'Install requirements-agent.txt')
class ScriptSecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.cases_path, self.manifest_path = write_synthetic_testset(
            root / 'testset', [make_case('dev-001'), make_case('dev-002'), make_case('test-001', split='test')])
        self.env_file = root / 'geheim' / 'apertus.env'
        self.env_file.parent.mkdir()
        self.env_file.write_text(f'LLM_API_KEY={SECRET_KEY}\n', encoding='utf-8')
        self.out = root / 'lauf'
        from scripts import run_agent_eval
        self.script = run_agent_eval

    def run_script(self, argv, wiring_):
        @contextlib.contextmanager
        def fake_wiring(mock, trace_dir):
            yield replace(wiring_, trace_dir=trace_dir)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(eval_runner, 'CASES_PATH', self.cases_path), \
                patch.object(eval_runner, 'MANIFEST_PATH', self.manifest_path), \
                patch.object(self.script, 'open_wiring', fake_wiring), \
                patch.object(config, 'LLM_API_KEY', SECRET_KEY), \
                patch.dict(os.environ, {'FACT_ATTACK_ENV_FILE': str(self.env_file)}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.script.main(argv)
        return code, stdout.getvalue() + stderr.getvalue()

    def test_runner_never_prints_the_key_file_path_or_value(self):
        def handler(request):  # an endpoint whose error message repeats the key and the key file
            return httpx.Response(500, json={'error': {'message': f'Schlüssel {SECRET_KEY} aus {self.env_file} '
                                                                  'abgelehnt'}})

        code, output = self.run_script(['--arm', 'b1', '--split', 'dev', '--max-requests', '24', '--out',
                                        str(self.out)],
                                       wiring(FakeEngine(), mock=False, transport=lambda: httpx.MockTransport(handler)))
        self.assertEqual(code, 0, output)
        self.assertTrue(records(self.out))
        for secret in (SECRET_KEY, str(self.env_file), self.env_file.name):
            self.assertNotIn(secret, output)
            for path in self.out.rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret, path.read_text(encoding='utf-8'), path)
        self.assertTrue(all(record['outcome'] == 'technischer_fehler' and record['error']
                            for record in records(self.out)))
        self.assertIn('***', records(self.out)[0]['error'])  # the message did carry the key: it was redacted

    def test_real_run_cap_may_not_exceed_the_per_check_cap_times_the_cases(self):
        code, output = self.run_script(['--arm', 'b1', '--split', 'dev', '--max-requests',
                                        str(2 * settings.MAX_REQUESTS + 1), '--out', str(self.out)],
                                       wiring(FakeEngine(), mock=False))
        self.assertEqual(code, 1)
        self.assertIn('--max-requests', output)
        self.assertFalse(self.out.exists())

    def test_real_run_needs_the_key_file_variable(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('FACT_ATTACK_ENV_FILE', None)
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(eval_runner, 'CASES_PATH', self.cases_path), \
                    patch.object(eval_runner, 'MANIFEST_PATH', self.manifest_path), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = self.script.main(['--arm', 'b1', '--split', 'dev', '--max-requests', '5', '--out',
                                         str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn('FACT_ATTACK_ENV_FILE', stderr.getvalue())
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
