"""Configuration freeze and the one-time test run: config hash, clean tree, freeze file, test-run marker.

git is faked (`eval_runner._git`), the testset is a synthetic locked set in a temporary directory, the arms run on
fakes with the scripted decider. The real `data/genai/testset/cases.jsonl` is never opened: `open` is guarded.
"""
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import settings
from src.agent import testset as testset_module
from tests.test_agent_graph import BOOKLETS, CLEAR, HAS_LANGGRAPH, FakeEngine, FakeIndex
from tests.test_agent_eval_runner import make_case, records, write_synthetic_testset

if HAS_LANGGRAPH:
    from src.agent import eval_runner

REAL_CASES = testset_module.CASES_PATH.resolve()


class FakeGit:
    """`git rev-parse` and `git status --porcelain -z` of a repository whose state the test sets."""

    def __init__(self):
        self.status = ''
        self.src_tree = 'baum-src-1'

    def __call__(self, *args):
        if args[0] == 'status':
            return self.status
        if args == ('rev-parse', 'HEAD'):
            return 'c0ffee\n'
        if args[0] == 'rev-parse' and args[1].endswith(':track_2a/src'):
            return self.src_tree + '\n'
        if args[0] == 'rev-parse' and args[1].endswith(':track_2a/scripts'):
            return 'baum-scripts-1\n'
        raise AssertionError(f'unerwarteter git-Aufruf {args}')


@contextlib.contextmanager
def guarded_open():
    """Fails the test if anything opens the real test split; records the attempt."""
    attempts = []
    originals = {'builtins': builtins.open, 'io': io.open}

    def guard(original):
        def opener(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)) and Path(os.fsdecode(file)).resolve() == REAL_CASES:
                attempts.append(file)
                raise AssertionError('Der echte Testteil wurde geöffnet')
            return original(file, *args, **kwargs)
        return opener

    with patch.object(builtins, 'open', guard(originals['builtins'])), patch.object(io, 'open', guard(originals['io'])):
        yield attempts


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.test_ids = ['test-001', 'test-002']
        self.cases_path, self.manifest_path = write_synthetic_testset(
            self.root / 'testset', [make_case('dev-001'), make_case('test-001', split='test'),
                                    make_case('test-002', split='test')])
        self.git = FakeGit()
        self.freeze = self.root / 'results' / 'config_freeze.json'
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(eval_runner, '_git', self.git))
        stack.enter_context(patch.object(eval_runner, 'CASES_PATH', self.cases_path))
        stack.enter_context(patch.object(eval_runner, 'MANIFEST_PATH', self.manifest_path))
        self.opened = stack.enter_context(guarded_open())
        from scripts import freeze_config, run_agent_eval
        self.script, self.freeze_script = run_agent_eval, freeze_config

    def dev_run(self, **overrides):
        """A finished real dev evaluation, as the runner leaves it."""
        path = self.root / 'dev-lauf'
        path.mkdir(exist_ok=True)
        summary = {'split': 'dev', 'mock': False, 'tree_clean': True, 'config_hash': eval_runner.config_hash(),
                   'arm': 'b1', **overrides}
        (path / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
        return path

    def frozen(self):
        eval_runner.write_freeze(self.freeze, self.dev_run())
        return self.freeze

    def run_script(self, *argv, arm='b1', engine_factory=FakeEngine):
        @contextlib.contextmanager
        def fake_wiring(mock, trace_dir):
            yield eval_runner.Wiring(index=FakeIndex(CLEAR), booklets_dir=BOOKLETS, mock=True,
                                     engine_factory=engine_factory, trace_dir=trace_dir)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.script, 'open_wiring', fake_wiring), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = self.script.main(['--arm', arm, '--max-requests', '24', '--mock', *argv])
        return code, stdout.getvalue() + stderr.getvalue()

    def recorded_runs(self):
        return json.loads(self.freeze.read_text(encoding='utf-8'))['test_runs']

    def split_args(self, out, freeze=None):
        return ['--split', 'test', '--out', str(out)] + (['--freeze', str(freeze)] if freeze else [])

    def test_test_split_needs_a_matching_freeze_file(self):
        with patch.object(eval_runner, 'load_testset', wraps=eval_runner.load_testset) as load:
            code, output = self.run_script(*self.split_args(self.root / 'a'))
            self.assertEqual(code, 1)
            self.assertIn('--freeze', output)
            code, _ = self.run_script(*self.split_args(self.root / 'b', self.root / 'fehlt.json'))
            self.assertEqual(code, 1)

            self.frozen()
            stale = json.loads(self.freeze.read_text(encoding='utf-8'))
            stale['config_hash'] = '0' * 64
            self.freeze.write_text(json.dumps(stale), encoding='utf-8')
            code, output = self.run_script(*self.split_args(self.root / 'c', self.freeze))
            self.assertEqual(code, 1)
            self.assertEqual(self.recorded_runs(), {})
            load.assert_not_called()
        for name in ('a', 'b', 'c'):
            self.assertFalse((self.root / name).exists())
        self.assertEqual(self.opened, [])

    def test_test_split_refuses_a_second_run(self):
        self.frozen()
        first = self.root / 'erster'
        with patch.object(eval_runner, 'load_testset', wraps=eval_runner.load_testset) as load:
            code, output = self.run_script(*self.split_args(first, self.freeze))
            self.assertEqual(code, 0, output)
            code, output = self.run_script(*self.split_args(self.root / 'zweiter', self.freeze))
            self.assertEqual(code, 1)  # a second new run of the same arm
            self.assertIn('bereits', output)
        self.assertEqual(load.call_count, 1)
        self.assertFalse((self.root / 'zweiter').exists())
        self.assertEqual(self.recorded_runs()['b1']['out_dir'], str(first.resolve()))
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.mark_test_run(self.freeze, 'b1', out_dir=self.root / 'dritter')
        # the marker file holds even if the entry is removed from the freeze file
        cleaned = json.loads(self.freeze.read_text(encoding='utf-8'))
        cleaned['test_runs'] = {}
        self.freeze.write_text(json.dumps(cleaned), encoding='utf-8')
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.require_freeze(self.freeze, 'b1')
        with self.assertRaises(eval_runner.FreezeError):  # without an arm: no arm may have run
            eval_runner.require_freeze(self.freeze)
        self.assertEqual(self.opened, [])

    def test_each_arm_runs_the_test_split_once(self):
        self.frozen()
        for arm in ('b1', 'b2', 'b0'):
            with self.subTest(arm=arm):
                code, output = self.run_script(*self.split_args(self.root / arm, self.freeze), arm=arm)
                self.assertEqual(code, 0, output)
                code, _ = self.run_script(*self.split_args(self.root / f'{arm}-nochmal', self.freeze), arm=arm)
                self.assertEqual(code, 1)
                self.assertFalse((self.root / f'{arm}-nochmal').exists())
        self.assertEqual(sorted(self.recorded_runs()), ['b0', 'b1', 'b2'])
        self.assertEqual(sorted(path.name for path in self.freeze.parent.glob('*.test_run.*')),
                         ['config_freeze.json.test_run.b0', 'config_freeze.json.test_run.b1',
                          'config_freeze.json.test_run.b2'])
        self.assertEqual(self.opened, [])

    def test_aborted_test_run_is_only_resumed_in_its_own_out_dir(self):
        self.frozen()
        out = self.root / 'test-b1'
        made = []

        def failing_second_engine():
            made.append(True)
            if len(made) == 2:  # the check of the second case breaks off the campaign
                raise RuntimeError('Abbruch')
            return FakeEngine()

        with patch.object(eval_runner, 'load_testset', wraps=eval_runner.load_testset) as load:
            code, output = self.run_script(*self.split_args(out, self.freeze),
                                           engine_factory=failing_second_engine)
            self.assertEqual(code, 1, output)
            self.assertEqual([record['id'] for record in records(out)], self.test_ids[:1])

            code, _ = self.run_script('--resume', *self.split_args(out, self.freeze), arm='b2')
            self.assertEqual(code, 1)  # no test run of b2 to resume
            other = self.root / 'anderswo'
            code, output = self.run_script('--resume', *self.split_args(other, self.freeze))
            self.assertEqual(code, 1)  # b1's run belongs to its own out_dir
            self.assertFalse(other.exists())
            code, _ = self.run_script(*self.split_args(out, self.freeze))
            self.assertEqual(code, 1)  # a new run into the same folder is no resume

            resumed = []

            def counting_engine():
                resumed.append(True)
                return FakeEngine()

            code, output = self.run_script('--resume', *self.split_args(out, self.freeze),
                                           engine_factory=counting_engine)
            self.assertEqual(code, 0, output)
        self.assertEqual(len(resumed), 1)  # only test-002 was checked
        self.assertEqual([record['id'] for record in records(out)], self.test_ids)  # test-001 not checked again
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['finished'], summary['cases']), (2, 2))
        self.assertEqual(load.call_count, 2)  # the first run and the resume, nothing else
        self.assertEqual(list(self.recorded_runs()), ['b1'])

        self.git.status = ' M track_2a/src/agent/graph.py\0'  # a dirty tree or a changed configuration blocks too
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.require_freeze(self.freeze, 'b1', resume_out=out)
        self.git.status = ''
        with patch.object(settings, 'GUARD_SCORE_MARGIN', 0.02), self.assertRaises(eval_runner.FreezeError):
            eval_runner.require_freeze(self.freeze, 'b1', resume_out=out)
        self.assertEqual(eval_runner.require_freeze(self.freeze, 'b1', resume_out=out)['test_runs']['b1']['arm'],
                         'b1')
        self.assertEqual(self.opened, [])

    def test_changed_prompt_or_threshold_changes_the_hash_and_blocks_the_test_split(self):
        frozen_hash = eval_runner.config_hash()
        self.frozen()
        self.assertEqual(eval_runner.config_hash(), frozen_hash)
        with patch.object(settings, 'GUARD_SCORE_MARGIN', 0.02):
            self.assertNotEqual(eval_runner.config_hash(), frozen_hash)
            with self.assertRaises(eval_runner.FreezeError):
                eval_runner.require_freeze(self.freeze)
            code, _ = self.run_script(*self.split_args(self.root / 'schwelle', self.freeze))
            self.assertEqual(code, 1)
        prompts = self.root / 'prompts.py'
        prompts.write_bytes(eval_runner.PROMPTS_PATH.read_bytes() + b'\n# geaendert\n')
        with patch.object(eval_runner, 'PROMPTS_PATH', prompts):
            self.assertNotEqual(eval_runner.config_hash(), frozen_hash)
            with self.assertRaises(eval_runner.FreezeError):
                eval_runner.require_freeze(self.freeze)
        for name, value in (('TOOL_MODE', 'native'), ('GUARD_MIN_HITS_RATIO', 0.5), ('GUARD_NONE_BELOW', 0.5)):
            with self.subTest(name=name), patch.object(settings, name, value):
                self.assertNotEqual(eval_runner.config_hash(), frozen_hash)
        with patch.object(eval_runner.config, 'LLM_NAME', 'anderes-modell'):
            self.assertNotEqual(eval_runner.config_hash(), frozen_hash)
        self.git.src_tree = 'baum-src-2'  # a new commit under src/
        self.assertNotEqual(eval_runner.config_hash(), frozen_hash)
        self.assertEqual(self.recorded_runs(), {})

    def test_dirty_tree_blocks_the_freeze(self):
        dev = self.dev_run()
        self.git.status = ' M track_2a/src/agent/graph.py\0'
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.write_freeze(self.freeze, dev)
        self.assertFalse(self.freeze.exists())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = self.freeze_script.main(['--out', str(self.freeze), '--dev-run', str(dev)])
        self.assertEqual(code, 1)
        self.assertFalse(self.freeze.exists())

        self.git.status = '?? track_2a/results/genai/agent_eval/dev/cases.jsonl\0'  # results do not count
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.freeze_script.main(['--out', str(self.freeze), '--dev-run', str(dev)]), 0)
        freeze = json.loads(self.freeze.read_text(encoding='utf-8'))
        self.assertEqual(freeze['dev_run'], str(dev))
        self.assertEqual(freeze['git_commit'], 'c0ffee')
        self.git.status = '?? track_2a/scripts/neu.py\0'  # an untracked script is not in the tree hash
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.require_freeze(self.freeze)

    def test_no_secret_enters_the_hash_input_or_the_freeze_file(self):
        key = 'sk-geheim-0123456789abcdef'
        env_file = str(self.root / 'geheim' / 'apertus.env')
        with patch.object(eval_runner.config, 'LLM_API_KEY', key), \
                patch.dict(os.environ, {'FACT_ATTACK_ENV_FILE': env_file}), \
                patch.object(eval_runner.config, 'A_PROMPT', f'Prompt mit {key}'), \
                patch.object(eval_runner.config, 'B_PROMPT', env_file):  # secrets under harmless names too
            snapshot = json.dumps(eval_runner._settings_snapshot(), default=str)
            components = json.dumps(eval_runner.config_components(), default=str)
            self.frozen()
            frozen = self.freeze.read_text(encoding='utf-8')
            for secret in (key, env_file, 'apertus.env'):
                for text in (snapshot, components, frozen):
                    self.assertNotIn(secret, text)
            self.assertNotIn('"src.config.LLM_API_KEY"', snapshot)
            self.assertNotIn('"src.config.LLM_BASE_URL"', snapshot)
            self.assertNotIn('"src.config.A_PROMPT"', snapshot)  # its value contains the key: left out
        self.assertIn('"src.config.LLM_NAME"', snapshot)

    def test_freeze_needs_a_real_dev_evaluation_with_this_configuration(self):
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.write_freeze(self.freeze, self.root / 'kein-lauf')
        for override in ({'mock': True}, {'split': 'test'}, {'config_hash': 'alt'}, {'tree_clean': False}):
            with self.subTest(override=override), self.assertRaises(eval_runner.FreezeError):
                eval_runner.write_freeze(self.freeze, self.dev_run(**override))
        self.assertFalse(self.freeze.exists())
        self.frozen()
        with self.assertRaises(eval_runner.FreezeError):  # never overwritten
            eval_runner.write_freeze(self.freeze, self.dev_run())

    def test_mock_rehearsal_on_a_synthetic_test_set_in_tmp_reads_no_real_test_case(self):
        self.frozen()
        out = self.root / 'probe'
        with patch.object(eval_runner, 'load_testset', wraps=eval_runner.load_testset) as load:
            code, output = self.run_script(*self.split_args(out, self.freeze))
        self.assertEqual(code, 0, output)
        load.assert_called_once()
        self.assertEqual(load.call_args.args, ('test',))
        self.assertEqual(load.call_args.kwargs['cases_path'], self.cases_path)
        self.assertTrue(load.call_args.kwargs['allow_test'])
        self.assertEqual([record['id'] for record in records(out)], self.test_ids)
        self.assertEqual(list(self.recorded_runs()), ['b1'])
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['split'], summary['mock'], summary['finished']), ('test', True, 2))
        self.assertEqual(self.opened, [])  # the real data/genai/testset/cases.jsonl was never opened

    def test_freeze_functions_refuse_the_regression_arms_and_touch_nothing(self):
        self.frozen()
        before = self.freeze.read_text(encoding='utf-8')
        for arm in ('regression-b0', 'regression-agent'):
            for call in (lambda: eval_runner.require_freeze(self.freeze, arm),
                         lambda: eval_runner.require_freeze(self.freeze, arm, resume_out=self.root / 'x'),
                         lambda: eval_runner.mark_test_run(self.freeze, arm, out_dir=self.root / 'x'),
                         lambda: eval_runner.open_test_split(self.freeze, arm, out_dir=self.root / 'x'),
                         lambda: eval_runner.open_test_split(self.freeze, arm, out_dir=self.root / 'x', resume=True)):
                with self.subTest(arm=arm), patch.object(eval_runner, '_read_freeze') as read, \
                        patch.object(eval_runner, 'load_testset') as load, \
                        self.assertRaises(eval_runner.FreezeError) as refused:
                    call()
                read.assert_not_called()  # nothing was read, not even the freeze file
                load.assert_not_called()
                self.assertIn(arm, str(refused.exception))
        self.assertEqual(sorted(self.freeze.parent.glob('*.test_run.*')), [])  # no marker
        self.assertEqual(self.freeze.read_text(encoding='utf-8'), before)
        self.assertEqual(self.opened, [])

    def test_the_script_records_the_split_in_campaign_json(self):
        self.frozen()
        out = self.root / 'test-b1'
        code, output = self.run_script(*self.split_args(out, self.freeze))
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads((out / 'campaign.json').read_text(encoding='utf-8'))['split'], 'test')

    def test_mock_run_on_the_real_test_split_is_refused(self):
        self.frozen()
        with patch.object(eval_runner, 'CASES_PATH', testset_module.CASES_PATH), \
                patch.object(eval_runner, 'load_testset') as load:
            code, output = self.run_script(*self.split_args(self.root / 'echt', self.freeze))
        self.assertEqual(code, 1)
        self.assertIn('--mock', output)
        load.assert_not_called()
        self.assertEqual(self.recorded_runs(), {})
        self.assertEqual(self.opened, [])


if __name__ == '__main__':
    unittest.main()
