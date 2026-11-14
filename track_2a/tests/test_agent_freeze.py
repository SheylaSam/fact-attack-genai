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

    def run_script(self, *argv):
        @contextlib.contextmanager
        def fake_wiring(mock, trace_dir):
            yield eval_runner.Wiring(index=FakeIndex(CLEAR), booklets_dir=BOOKLETS, mock=True,
                                     engine_factory=FakeEngine, trace_dir=trace_dir)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.script, 'open_wiring', fake_wiring), contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            code = self.script.main(['--arm', 'b1', '--max-requests', '24', '--mock', *argv])
        return code, stdout.getvalue() + stderr.getvalue()

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
            self.assertNotIn('test_run', json.loads(self.freeze.read_text(encoding='utf-8')))
            load.assert_not_called()
        for name in ('a', 'b', 'c'):
            self.assertFalse((self.root / name).exists())
        self.assertEqual(self.opened, [])

    def test_test_split_refuses_a_second_run(self):
        self.frozen()
        with patch.object(eval_runner, 'load_testset', wraps=eval_runner.load_testset) as load:
            code, output = self.run_script(*self.split_args(self.root / 'erster', self.freeze))
            self.assertEqual(code, 0, output)
            code, output = self.run_script(*self.split_args(self.root / 'zweiter', self.freeze))
            self.assertEqual(code, 1)
        self.assertEqual(load.call_count, 1)
        self.assertFalse((self.root / 'zweiter').exists())
        self.assertIn('test_run', json.loads(self.freeze.read_text(encoding='utf-8')))
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.mark_test_run(self.freeze)
        # the marker file holds even if the entry is removed from the freeze file
        cleaned = json.loads(self.freeze.read_text(encoding='utf-8'))
        cleaned.pop('test_run')
        self.freeze.write_text(json.dumps(cleaned), encoding='utf-8')
        with self.assertRaises(eval_runner.FreezeError):
            eval_runner.require_freeze(self.freeze)
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
        self.assertNotIn('test_run', json.loads(self.freeze.read_text(encoding='utf-8')))

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
        freeze = json.loads(self.freeze.read_text(encoding='utf-8'))
        self.assertIn('test_run', freeze)
        summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['split'], summary['mock'], summary['finished']), ('test', True, 2))
        self.assertEqual(self.opened, [])  # the real data/genai/testset/cases.jsonl was never opened

    def test_mock_run_on_the_real_test_split_is_refused(self):
        self.frozen()
        with patch.object(eval_runner, 'CASES_PATH', testset_module.CASES_PATH), \
                patch.object(eval_runner, 'load_testset') as load:
            code, output = self.run_script(*self.split_args(self.root / 'echt', self.freeze))
        self.assertEqual(code, 1)
        self.assertIn('--mock', output)
        load.assert_not_called()
        self.assertNotIn('test_run', json.loads(self.freeze.read_text(encoding='utf-8')))
        self.assertEqual(self.opened, [])


if __name__ == '__main__':
    unittest.main()
