"""Evaluation script over campaign result folders (scripts/evaluate_agent_runs.py).

Offline and synthetic: the campaigns are written by the real runner with fake arms into a temporary directory, the
testset is a synthetic locked set in tmp. The real data/genai/testset/cases.jsonl is never opened (`guarded_open`).
"""
import contextlib
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_agent_freeze import guarded_open
from tests.test_agent_graph import HAS_LANGGRAPH
from tests.test_agent_eval_runner import make_case, write_synthetic_testset
from tests.test_agent_regression import case_for

if HAS_LANGGRAPH:
    from src.agent import eval_metrics, eval_runner
    from src.agent.eval_metrics import CaseRun
    from src.agent.regression import CaseOutcome

REGRESSION_ARMS = ('regression-b0', 'regression-agent')


def dev_cases():
    return [replace(make_case('dev-001', expected='gestuetzt'), novelty='bekannt'),
            replace(make_case('dev-002', expected='widersprochen'), novelty='beobachtet'),
            replace(make_case('dev-003', expected='nicht_belegt'), novelty='ungesehen'),
            replace(make_case('dev-004', expected='gestuetzt'), novelty='ungesehen'),
            make_case('sdev-01', vote_id=None, group='spezial', kind='ohne_abstimmung', expected='quelle_fehlt')]


def held_out_cases():
    return [make_case('test-001', split='test', expected='gestuetzt'),
            make_case('test-002', split='test', expected='widersprochen')]


B1_OUTCOMES = {'dev-001': 'gestuetzt', 'dev-002': 'widersprochen', 'dev-003': 'gestuetzt',  # dev-003 is wrong
               'dev-004': 'technischer_fehler', 'sdev-01': 'quelle_fehlt'}


def fake_arm(arm, outcomes, requests=2):
    """A builder that sends `requests` requests per case and answers with the planned outcome."""
    def build(case, budget):
        outcome = outcomes[case.id]
        if outcome != eval_runner.NOT_SCORED:
            for _ in range(requests):
                budget.reserve(b'anfrage')
        valid = True if outcome in ('gestuetzt', 'widersprochen') else None
        return CaseRun(case=case, arm=arm, outcome=outcome, asked=False, options=(), final_vote_id=case.vote_id,
                       evidence_valid=valid, tokens=10, latency_ms=5.0, tool_calls=1,
                       error='Endpunkt antwortet nicht' if outcome == 'technischer_fehler' else None)
    return build


def regression_arm(arm, outcomes):
    def build(case, budget):
        budget.reserve(b'anfrage')
        outcome = outcomes[case.id]
        return CaseOutcome(id=case.id, arm=arm, outcome=outcome, label=case.label,
                           error='Zeitüberschreitung' if outcome == 'technischer_fehler' else None)
    return build


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class EvaluateRunsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cases_path, self.manifest_path = write_synthetic_testset(self.root / 'testset',
                                                                      dev_cases() + held_out_cases())
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(eval_runner, 'CASES_PATH', self.cases_path))
        stack.enter_context(patch.object(eval_runner, 'MANIFEST_PATH', self.manifest_path))
        self.opened = stack.enter_context(guarded_open())
        from scripts import evaluate_agent_runs
        self.script = evaluate_agent_runs
        self.freeze = self.root / 'config_freeze.json'

    # --- helpers ------------------------------------------------------------------------------------------------

    def campaign(self, name, arm, outcomes=None, *, cases=None, split='dev', cap=1000, requests=2, **kwargs):
        cases = dev_cases() if cases is None else cases
        out = self.root / name
        runs = eval_runner.run_campaign(arm, cases, build_arm=fake_arm(arm, outcomes or B1_OUTCOMES, requests),
                                        max_requests=cap, out_dir=out, split=split, **kwargs)
        return out, runs

    def evaluate(self, *runs, extra=(), prefix='bericht'):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.script.main(['--runs', *map(str, runs), '--out', str(self.root / prefix), *extra])
        return code, stdout.getvalue() + stderr.getvalue()

    def report(self, *runs, extra=(), prefix='bericht'):
        code, output = self.evaluate(*runs, extra=extra, prefix=prefix)
        self.assertEqual(code, 0, output)
        markdown = (self.root / f'{prefix}.md').read_text(encoding='utf-8')
        data = json.loads((self.root / f'{prefix}.json').read_text(encoding='utf-8'))
        return markdown, data

    def freeze_with(self, arm, out_dir):
        self.freeze.write_text(json.dumps({'config_hash': 'x', 'test_runs': {
            arm: {'arm': arm, 'out_dir': str(Path(out_dir).resolve())}}}), encoding='utf-8')

    # --- b0 / b1 / b2 campaigns ---------------------------------------------------------------------------------

    def test_a_dev_campaign_gets_every_task_1_metric_and_two_distinct_error_rates(self):
        out, runs = self.campaign('dev-b1', 'b1')
        markdown, data = self.report(out)
        [report] = data['campaigns']
        metrics = report['metrics']
        expected = eval_runner.scored(runs)
        self.assertEqual((report['arm'], report['split']), ('b1', 'dev'))
        self.assertAlmostEqual(metrics['f1_strict'], eval_metrics.macro_f1_strict(expected))
        f1, coverage = eval_metrics.macro_f1_answered(expected)
        self.assertAlmostEqual(metrics['f1_answered'], f1)
        self.assertAlmostEqual(metrics['coverage'], coverage)
        self.assertEqual(coverage, 0.75)
        self.assertEqual((metrics['wrong_judgements'], metrics['abstentions']), (1, 1))
        # two different rates: wrong among the answered (1 of 3) and technical errors over all runs (1 of 5)
        self.assertAlmostEqual(metrics['wrong_judgement_rate_answered'], 1 / 3)
        self.assertAlmostEqual(metrics['technical_error_rate_all_runs'], 1 / 5)
        self.assertEqual(metrics['evidence_validity'], 1.0)
        self.assertEqual(metrics['source_finding']['right_vote'], eval_metrics.source_finding(expected)['right_vote'])
        self.assertEqual(metrics['cost']['tokens'], 50)
        self.assertEqual([row['id'] for row in report['special_cases']], ['sdev-01'])
        self.assertIn('Fehlurteilquote (beantwortet)', markdown)
        self.assertIn('Technische Fehlerquote (alle Läufe)', markdown)
        self.assertIn('sdev-01', markdown)
        self.assertNotIn('Orakel', markdown)
        self.assertEqual(self.opened, [])

    def test_b0_source_finding_is_marked_as_oracle(self):
        outcomes = {**B1_OUTCOMES, 'sdev-01': eval_runner.NOT_SCORED}
        out, _ = self.campaign('dev-b0', 'b0', outcomes)
        markdown, data = self.report(out)
        self.assertIn('Orakel (nicht aussagekräftig)', markdown)
        self.assertEqual(data['campaigns'][0]['metrics']['source_finding']['note'], 'Orakel (nicht aussagekräftig)')

    def test_not_scored_and_not_run_cases_are_left_out_of_the_scores_and_counted(self):
        outcomes = {**B1_OUTCOMES, 'sdev-01': eval_runner.NOT_SCORED}
        # cap 6: dev-001..dev-003 use 6 requests; dev-004 is cut off by the cap and so is not run, nor sdev-01
        out, runs = self.campaign('dev-b0', 'b0', outcomes, cap=6)
        self.assertEqual([run.outcome for run in runs[3:]], [eval_runner.NOT_RUN] * 2)
        markdown, data = self.report(out)
        [report] = data['campaigns']
        self.assertEqual((report['not_run'], report['not_scored'], report['finished']), (2, 0, 3))
        self.assertEqual(report['cases_total'], 5)
        self.assertAlmostEqual(report['metrics']['f1_strict'],
                               eval_metrics.macro_f1_strict([run for run in runs[:3]]))
        self.assertEqual(report['metrics']['coverage'], 1.0)  # answered / answerable among the cases that ran
        # shown beside it: what the strict F1 would be with the cases that did not run counted as abstentions
        self.assertAlmostEqual(report['f1_strict_with_not_run'], eval_metrics.macro_f1_strict(runs))
        self.assertLess(report['f1_strict_with_not_run'], report['metrics']['f1_strict'])
        self.assertIn('nicht gelaufen', markdown)

        out, _ = self.campaign('dev-b0-voll', 'b0', outcomes)
        _, data = self.report(out, prefix='voll')
        [full] = data['campaigns']
        self.assertEqual((full['not_scored'], full['not_run'], full['finished']), (1, 0, 4))
        self.assertEqual(full['outcomes'].get(eval_runner.NOT_SCORED), 1)
        self.assertEqual([row['id'] for row in full['special_cases']], [])  # the not-scored case is no row

    def test_requests_per_run_come_from_the_journals(self):
        out, _ = self.campaign('dev-b1', 'b1', requests=3)
        (out / 'journals' / 'dev-002.requests.jsonl').write_text('{"sequence": 1}\n' * 5, encoding='utf-8')
        # the cases.jsonl "requests" say 3 for dev-002; the journal (what was really sent) says 5
        _, data = self.report(out)
        requests = data['campaigns'][0]['requests']
        self.assertEqual(requests['by_case']['dev-002'], 5)
        self.assertEqual(requests['by_case']['dev-001'], 3)
        self.assertEqual((requests['total'], requests['max_per_run']), (3 * 4 + 5, 5))
        self.assertAlmostEqual(requests['mean_per_run'], 17 / 5)  # over the five finished runs

    def test_novelty_is_reported_per_group(self):
        out, runs = self.campaign('dev-b1', 'b1')
        markdown, data = self.report(out)
        novelty = data['campaigns'][0]['novelty']
        self.assertEqual(sorted(novelty), ['bekannt', 'beobachtet', 'keine', 'ungesehen'])
        self.assertEqual(novelty['ungesehen']['answerable'], 2)
        self.assertEqual(novelty['ungesehen']['coverage'], 0.5)  # dev-004 ended technical
        self.assertEqual(novelty['bekannt']['f1_strict'],
                         eval_metrics.macro_f1_strict([run for run in runs if run.case.novelty == 'bekannt']))
        self.assertIn('ungesehen', markdown)

    def test_an_unterminated_last_line_of_a_crashed_run_is_ignored(self):
        out, _ = self.campaign('dev-b1', 'b1')
        path = out / 'cases.jsonl'
        path.write_text(path.read_text(encoding='utf-8') + '{"id": "dev-00', encoding='utf-8')
        _, data = self.report(out)
        self.assertEqual(data['campaigns'][0]['finished'], 5)

    def test_a_campaign_of_other_cases_is_refused_not_mis_scored(self):
        out, _ = self.campaign('dev-b1', 'b1', cases=dev_cases()[:3],
                               outcomes=B1_OUTCOMES)  # three of the five dev cases: another case list
        code, output = self.evaluate(out)
        self.assertEqual(code, 1)
        self.assertIn('Fälle', output)
        self.assertFalse((self.root / 'bericht.md').exists())

    def test_output_files_are_never_overwritten(self):
        out, _ = self.campaign('dev-b1', 'b1')
        (self.root / 'bericht.json').write_text('alt', encoding='utf-8')
        code, output = self.evaluate(out)
        self.assertEqual(code, 1)
        self.assertIn('bericht.json', output)
        self.assertEqual((self.root / 'bericht.json').read_text(encoding='utf-8'), 'alt')
        self.assertFalse((self.root / 'bericht.md').exists())  # nothing half-written
        (self.root / 'zwei.md').write_text('alt', encoding='utf-8')
        code, _ = self.evaluate(out, prefix='zwei')
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'zwei.json').exists())

    def test_a_folder_that_is_no_campaign_is_refused(self):
        (self.root / 'leer').mkdir()
        code, output = self.evaluate(self.root / 'leer')
        self.assertEqual(code, 1)
        self.assertIn('campaign.json', output)

    # --- the test split gate ------------------------------------------------------------------------------------

    def test_a_dev_campaign_never_reads_the_test_split(self):
        out, _ = self.campaign('dev-b1', 'b1')
        with patch.object(self.script, 'load_testset', wraps=self.script.load_testset) as load:
            self.report(out)
        load.assert_called_once()
        self.assertEqual(load.call_args.args, ('dev',))
        self.assertNotIn('allow_test', load.call_args.kwargs)
        self.assertEqual(self.opened, [])

    def test_a_dev_campaign_without_a_split_is_refused_not_guessed(self):
        out, _ = self.campaign('alt', 'b1', split=None)
        with patch.object(self.script, 'load_testset') as load:
            code, output = self.evaluate(out)
        self.assertEqual(code, 1)
        self.assertIn('Split', output)
        load.assert_not_called()

    def test_a_dev_campaign_without_a_split_in_campaign_json_may_name_it_in_summary_json(self):
        out, _ = self.campaign('alt', 'b1', split=None)
        (out / 'summary.json').write_text(json.dumps({'arm': 'b1', 'split': 'dev', 'mock': True}), encoding='utf-8')
        _, data = self.report(out)
        self.assertEqual((data['campaigns'][0]['split'], data['campaigns'][0]['mock']), ('dev', True))

    def test_a_test_campaign_needs_the_freeze_to_record_that_arms_test_run(self):
        outcomes = {'test-001': 'gestuetzt', 'test-002': 'widersprochen'}
        out, _ = self.campaign('test-b1', 'b1', outcomes, cases=held_out_cases(), split='test')
        with patch.object(self.script, 'load_testset') as load:
            code, output = self.evaluate(out)  # no --freeze
            self.assertEqual(code, 1)
            self.assertIn('--freeze', output)
            code, _ = self.evaluate(out, extra=['--freeze', str(self.freeze)])  # the freeze file is missing
            self.assertEqual(code, 1)
            self.freeze_with('b2', out)  # only another arm has a recorded test run
            code, _ = self.evaluate(out, extra=['--freeze', str(self.freeze)])
            self.assertEqual(code, 1)
            self.freeze_with('b1', self.root / 'anderswo')  # that arm's run belongs to another folder
            code, _ = self.evaluate(out, extra=['--freeze', str(self.freeze)])
            self.assertEqual(code, 1)
        load.assert_not_called()
        self.assertFalse((self.root / 'bericht.md').exists())
        self.assertEqual(self.opened, [])

    def test_a_recorded_path_from_another_checkout_with_the_same_results_suffix_is_accepted(self):
        outcomes = {'test-001': 'gestuetzt', 'test-002': 'widersprochen'}
        name = 'track_2a/results/genai/agent_eval_test_b1'
        out, _ = self.campaign(name, 'b1', outcomes, cases=held_out_cases(), split='test')
        self.freeze.write_text(json.dumps({'config_hash': 'x', 'test_runs': {'b1': {
            'arm': 'b1', 'out_dir': f'/somewhere/else/checkout/{name}'}}}), encoding='utf-8')
        with patch.object(self.script, 'load_testset', wraps=self.script.load_testset) as load:
            _, data = self.report(out, extra=['--freeze', str(self.freeze)])
        load.assert_called_once()
        self.assertEqual(data['campaigns'][0]['finished'], 2)

    def test_a_recorded_path_with_another_results_suffix_is_refused(self):
        outcomes = {'test-001': 'gestuetzt', 'test-002': 'widersprochen'}
        out, _ = self.campaign('track_2a/results/genai/agent_eval_test_b1', 'b1', outcomes,
                               cases=held_out_cases(), split='test')
        for recorded in ('/somewhere/else/checkout/track_2a/results/genai/agent_eval_test_b2',
                         '/somewhere/else/checkout/track_2a/results/genai/agent_eval_test_b1/sub',
                         '/somewhere/else/checkout/results/genai/agent_eval_test_b1'):
            self.freeze.write_text(json.dumps({'config_hash': 'x', 'test_runs': {'b1': {
                'arm': 'b1', 'out_dir': recorded}}}), encoding='utf-8')
            with patch.object(self.script, 'load_testset') as load:
                code, _ = self.evaluate(out, extra=['--freeze', str(self.freeze)])
            self.assertEqual(code, 1, recorded)
            load.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_a_test_campaign_with_a_recorded_test_run_is_evaluated_from_the_synthetic_test_split(self):
        outcomes = {'test-001': 'gestuetzt', 'test-002': 'widersprochen'}
        out, _ = self.campaign('test-b1', 'b1', outcomes, cases=held_out_cases(), split='test')
        self.freeze_with('b1', out)
        with patch.object(self.script, 'load_testset', wraps=self.script.load_testset) as load:
            _, data = self.report(out, extra=['--freeze', str(self.freeze)])
        load.assert_called_once()
        self.assertEqual(load.call_args.args, ('test',))
        self.assertTrue(load.call_args.kwargs['allow_test'])
        [report] = data['campaigns']
        self.assertEqual((report['split'], report['finished']), ('test', 2))
        self.assertEqual(report['metrics']['coverage'], 1.0)
        self.assertEqual(self.opened, [])  # the synthetic set, never the real file

    def test_test_case_fields_stored_in_the_records_are_used_without_reading_the_split(self):
        outcomes = {'test-001': 'gestuetzt', 'test-002': 'widersprochen'}
        out, _ = self.campaign('test-b1', 'b1', outcomes, cases=held_out_cases(), split='test')
        path = out / 'cases.jsonl'
        stored = []
        for line, case in zip(path.read_text(encoding='utf-8').splitlines(), held_out_cases()):
            stored.append(json.dumps({**json.loads(line), 'case': asdict(case)}))
        path.write_text('\n'.join(stored) + '\n', encoding='utf-8')
        with patch.object(self.script, 'load_testset') as load:
            _, data = self.report(out)  # no freeze needed: nothing is read from the test split
        load.assert_not_called()
        self.assertEqual(data['campaigns'][0]['finished'], 2)

    # --- the regression pair ------------------------------------------------------------------------------------

    def regression_pair(self, b0_outcomes, agent_outcomes, labels):
        cases = [case_for(case_id=case_id, label=label) for case_id, label in labels.items()]
        runs = []
        for arm, outcomes in zip(REGRESSION_ARMS, (b0_outcomes, agent_outcomes)):
            out = self.root / arm
            eval_runner.run_campaign(arm, cases, build_arm=regression_arm(arm, outcomes), max_requests=100,
                                     out_dir=out, split='regression')
            runs.append(out)
        return runs

    def test_a_regression_pair_lists_every_changed_case_and_the_verdict(self):
        labels = {'ost-1': 0, 'ost-2': 0, 'ost-3': 2, 'ost-4': 1}
        b0 = {'ost-1': 'gestuetzt', 'ost-2': 'gestuetzt', 'ost-3': 'nicht_belegt', 'ost-4': 'nicht_belegt'}
        agent = {'ost-1': 'gestuetzt', 'ost-2': 'nicht_belegt', 'ost-3': 'widersprochen', 'ost-4': 'nicht_belegt'}
        markdown, data = self.report(*self.regression_pair(b0, agent, labels))
        regression = data['regression']
        self.assertEqual([change['id'] for change in regression['changed']], ['ost-2', 'ost-3'])
        worse = regression['changed'][0]
        self.assertEqual((worse['b0'], worse['agent'], worse['direction']), ('gestuetzt', 'nicht_belegt', 'schlechter'))
        self.assertTrue(worse['reason'])
        self.assertEqual((regression['better'], regression['worse'], regression['net_worse']), (1, 1, 0))
        self.assertTrue(regression['passes'])
        self.assertEqual(regression['b0_correct'], 3)
        self.assertEqual(regression['agent_correct'], 3)
        self.assertEqual(data['campaigns'], [])  # the pair is one section, no metrics of the dev arms
        self.assertIn('ost-2', markdown)
        self.assertIn('bestanden', markdown.lower())
        self.assertNotIn('nicht bestanden', markdown.lower())

    def test_a_failing_regression_pair_says_so(self):
        labels = {f'ost-{n}': 0 for n in range(1, 4)}
        b0 = dict.fromkeys(labels, 'gestuetzt')
        agent = {'ost-1': 'nicht_belegt', 'ost-2': 'technischer_fehler', 'ost-3': 'gestuetzt'}
        markdown, data = self.report(*self.regression_pair(b0, agent, labels))
        self.assertFalse(data['regression']['passes'])
        self.assertEqual(data['regression']['net_worse'], 2)
        self.assertIn('nicht bestanden', markdown.lower())
        self.assertIn('Zeitüberschreitung', markdown)  # the agent's error is in the reason of its case

    def test_a_regression_arm_without_its_partner_is_refused(self):
        b0_dir, _ = self.regression_pair({'ost-1': 'gestuetzt'}, {'ost-1': 'gestuetzt'}, {'ost-1': 0})
        code, output = self.evaluate(b0_dir)
        self.assertEqual(code, 1)
        self.assertIn('regression-agent', output)

    def test_regression_cases_the_campaign_did_not_run_count_as_no_result(self):
        labels = {'ost-1': 0, 'ost-2': 0}
        cases = [case_for(case_id=case_id, label=label) for case_id, label in labels.items()]
        for arm, cap in zip(REGRESSION_ARMS, (100, 1)):  # the agent run stops after the first case
            eval_runner.run_campaign(arm, cases, build_arm=regression_arm(arm, dict.fromkeys(labels, 'gestuetzt')),
                                     max_requests=cap, out_dir=self.root / arm, split='regression')
        _, data = self.report(self.root / 'regression-b0', self.root / 'regression-agent')
        [change] = data['regression']['changed']
        self.assertEqual((change['id'], change['agent'], change['direction']),
                         ('ost-2', eval_runner.NOT_RUN, 'schlechter'))

    def test_the_report_carries_no_secret(self):
        out, _ = self.campaign('dev-b1', 'b1')
        with patch.object(eval_runner.config, 'LLM_API_KEY', 'Endpunkt'):  # a "key" that occurs in a record's error
            markdown, data = self.report(out)
        self.assertNotIn('Endpunkt', markdown)
        self.assertNotIn('Endpunkt', json.dumps(data, ensure_ascii=False))
        self.assertIn('***', markdown)  # the error of dev-004 is listed, redacted
        self.assertEqual(data['campaigns'][0]['errors'][0]['id'], 'dev-004')


if __name__ == '__main__':
    unittest.main()
