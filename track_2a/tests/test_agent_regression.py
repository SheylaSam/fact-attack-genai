"""Regression set and paired comparison of the agent evaluation (stage 5, task 5).

No network and no Apertus request, no real index: the fakes of test_agent_graph.py and test_agent_eval_runner.py, a
synthetic index of votes and PDFs written into a temporary directory. The 42 regression cases are dev data
(data/genai/ost_june_dev.jsonl); the test split is never touched.
"""
from collections import Counter
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_agent_graph import (BOOKLETS, CLAIM, CLEAR, HAS_LANGGRAPH, QUOTE, V1, FakeEngine, FakeIndex,
                                    make_prediction)
from tests.test_agent_eval_runner import B0Engine, records, wiring

if HAS_LANGGRAPH:
    from src.agent import eval_runner, regression
    from src.agent.regression import CaseOutcome, RegressionCase
    from src.agent.source_index import VoteRef

DE_TITLE_A = 'Änderung des Zivildienstgesetzes'
DE_TITLE_B = 'Volksinitiative «Keine 10-Millionen-Schweiz! (Nachhaltigkeitsinitiative)»'


def write_pdf(path, pages):
    import fitz
    with fitz.open() as document:
        for number in range(1, pages + 1):
            document.new_page().insert_text((72, 72), f'Seite {number}')
        document.save(path)


def case_for(source_id=V1.source_id, title=V1.title, case_id='ost-0001', label=0, language='de'):
    return RegressionCase(id=case_id, claim=CLAIM, claim_language=language, label=label, source_id=source_id,
                          vote_title=title)


def outcome(case_id, result, label=0, arm='regression-b0', error=None):
    return CaseOutcome(id=case_id, arm=arm, outcome=result, label=label, error=error)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class LoadTests(unittest.TestCase):
    def test_loads_the_42_regression_cases_with_their_languages(self):
        cases = regression.load_regression_cases()
        self.assertEqual(len(cases), 42)
        self.assertEqual(Counter(case.claim_language for case in cases), {'de': 15, 'fr': 16, 'it': 11})
        self.assertEqual(len({case.id for case in cases}), 42)
        self.assertTrue(all(case.label in (0, 1, 2) for case in cases))
        first = cases[0]
        self.assertEqual((first.id, first.source_id, first.label), ('ost-0000', '2026-06-14_fr', 0))

    def test_a_vote_title_is_only_set_for_the_german_booklets_the_index_knows(self):
        for case in regression.load_regression_cases():
            with self.subTest(case=case.id):
                if case.source_id.endswith('_de'):
                    self.assertIn(case.vote_title, (DE_TITLE_A, DE_TITLE_B))
                else:
                    self.assertIsNone(case.vote_title)  # French and Italian booklets are checked as a whole

    def test_a_broken_file_is_refused_not_shortened(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'dev.jsonl'
            lines = Path(regression.REGRESSION_PATH).read_text(encoding='utf-8').splitlines()
            path.write_text('\n'.join(lines[:41]) + '\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                regression.load_regression_cases(path)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class FixedVoteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for language in ('fr', 'it'):
            write_pdf(self.dir / f'2026-06-14_{language}.pdf', 3)
        self.votes = {vote.vote_id: vote for vote in (
            VoteRef('2026-06-14_de#1', '2026-06-14_de', '2026-06-14', 'de', DE_TITLE_A, (2, 3)),
            VoteRef('2026-06-14_de#2', '2026-06-14_de', '2026-06-14', 'de', DE_TITLE_B, (4, 5)))}
        self.catalog = {f'2026-06-14_{language}.pdf': {'date': '2026-06-14', 'language': language, 'votes': []}
                        for language in ('fr', 'it')}
        self.cases = regression.load_regression_cases()

    def test_every_case_gets_a_fixed_vote_or_a_clear_error(self):
        for case in self.cases:
            with self.subTest(case=case.id):
                vote = regression.fixed_vote_for(case, self.votes, self.dir, self.catalog)
                self.assertEqual(vote.source_id, case.source_id)
                if case.vote_title is not None:  # German: the vote of that title
                    self.assertEqual(vote.title, case.vote_title)
                    self.assertIn(vote.vote_id, self.votes)
                else:  # French and Italian: the whole booklet, every page, unknown to the index
                    self.assertEqual((vote.vote_id, vote.pages), (f'{case.source_id}#0', (1, 2, 3)))
                    self.assertNotIn(vote.vote_id, self.votes)

        (self.dir / '2026-06-14_fr.pdf').unlink()  # a booklet that is gone
        del self.catalog['2026-06-14_it.pdf']  # a booklet the catalog does not allow
        unknown_title = case_for('2026-06-14_de', 'Eine Vorlage, die es nicht gibt', case_id='ost-9999')
        failing = [case for case in self.cases if case.source_id in ('2026-06-14_fr', '2026-06-14_it')]
        self.assertTrue(failing)
        for case in failing + [unknown_title]:
            with self.subTest(failing=case.id), self.assertRaisesRegex(ValueError, case.id):
                regression.fixed_vote_for(case, self.votes, self.dir, self.catalog)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class ArmTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def run_arm(self, arm, cases, engine, *, name=None, **kwargs):
        build = eval_runner.make_arm(arm, wiring(engine, **kwargs))
        return eval_runner.run_campaign(arm, cases, build_arm=build, max_requests=12 * len(cases),
                                        out_dir=self.tmp / (name or arm))

    def test_regression_b0_uses_vote_none_on_the_cases_booklet(self):
        engine = B0Engine(prediction=make_prediction(0, [(2, QUOTE)]))
        index = FakeIndex(CLEAR)
        [run] = self.run_arm('regression-b0', [case_for(case_id='ost-0007', language='fr')], engine, index=index)
        [call] = engine.claim_calls
        self.assertEqual(call['booklet_pdf'], BOOKLETS / f'{V1.source_id}.pdf')  # the case's own booklet
        self.assertIsNone(call['vote'])  # the production path: "Automatisch"
        self.assertEqual((call['claim_language'], call['case_id']), ('fr', 'ost-0007'))  # the dataset's language
        self.assertEqual((engine.retrieve_calls, engine.verify_calls, index.vote_calls), ([], [], []))
        self.assertIsInstance(run, CaseOutcome)
        self.assertEqual((run.id, run.arm, run.outcome, run.label, run.error),
                         ('ost-0007', 'regression-b0', 'gestuetzt', 0, None))
        [record] = records(self.tmp / 'regression-b0')
        self.assertEqual((record['id'], record['outcome'], record['label']), ('ost-0007', 'gestuetzt', 0))

    def test_regression_b0_turns_an_error_into_a_technical_outcome(self):
        engine = B0Engine(prediction=make_prediction(0, [(2, QUOTE)], error='Zeitüberschreitung'))
        [run] = self.run_arm('regression-b0', [case_for()], engine)
        self.assertEqual((run.outcome, run.error), ('technischer_fehler', 'Zeitüberschreitung'))

    def test_regression_agent_checks_inside_the_given_vote_without_searching_for_votes(self):
        engine = FakeEngine(prediction=make_prediction(0, [(2, QUOTE)]))
        index = FakeIndex(CLEAR)
        [run] = self.run_arm('regression-agent', [case_for(case_id='ost-0003')], engine, index=index)
        self.assertEqual(index.vote_calls, [])  # the vote is given: no search for votes
        self.assertEqual([call['booklet_pdf'] for call in engine.verify_calls], [BOOKLETS / f'{V1.source_id}.pdf'])
        self.assertEqual((run.id, run.arm, run.outcome, run.label, run.vote_id, run.error),
                         ('ost-0003', 'regression-agent', 'gestuetzt', 0, V1.vote_id, None))

    def test_regression_agent_checks_a_french_booklet_as_a_whole(self):
        write_pdf(self.tmp / '2026-06-14_fr.pdf', 4)
        catalog = {'2026-06-14_fr.pdf': {'date': '2026-06-14', 'language': 'fr', 'votes': []}}
        engine = FakeEngine(prediction=make_prediction(0, [(2, QUOTE)]))
        setup = eval_runner.Wiring(index=FakeIndex(CLEAR), booklets_dir=self.tmp, mock=True, catalog=catalog,
                                   engine_factory=lambda: engine)
        case = case_for('2026-06-14_fr', None, language='fr')
        [run] = eval_runner.run_campaign('regression-agent', [case], max_requests=12, out_dir=self.tmp / 'fr',
                                         build_arm=eval_runner.make_arm('regression-agent', setup))
        self.assertEqual((run.outcome, run.vote_id, run.error), ('gestuetzt', '2026-06-14_fr#0', None))
        self.assertIsNone(engine.verify_calls[0]['vote'])  # the engine finds the vote inside the booklet

    def test_a_case_whose_vote_cannot_be_built_is_a_technical_error_not_a_gap(self):
        cases = [case_for(case_id='ost-0001'), case_for('2026-06-14_fr', None, case_id='ost-0002', language='fr')]
        runs = self.run_arm('regression-agent', cases, FakeEngine(prediction=make_prediction(0, [(2, QUOTE)])))
        self.assertEqual([run.id for run in runs], ['ost-0001', 'ost-0002'])  # nothing dropped
        self.assertEqual(runs[0].outcome, 'gestuetzt')
        self.assertEqual(runs[1].outcome, 'technischer_fehler')
        self.assertIn('ost-0002', runs[1].error)
        self.assertEqual([record['id'] for record in records(self.tmp / 'regression-agent')], ['ost-0001', 'ost-0002'])

    def test_regression_agent_with_a_claim_of_bad_length_is_a_technical_error_not_an_abort(self):
        from dataclasses import replace
        from src.agent import settings
        for claim in ('', 'x' * (settings.MAX_CLAIM_CHARS + 1)):
            with self.subTest(length=len(claim)):
                cases = [replace(case_for(case_id='ost-0001'), claim=claim), case_for(case_id='ost-0002')]
                bad, good = self.run_arm('regression-agent', cases,
                                         FakeEngine(prediction=make_prediction(0, [(2, QUOTE)])),
                                         name=f'laenge-{len(claim)}')
                self.assertEqual(bad.outcome, 'technischer_fehler')
                self.assertIn('ost-0001', bad.error)
                self.assertEqual(good.outcome, 'gestuetzt')
                self.assertEqual([r['id'] for r in records(self.tmp / f'laenge-{len(claim)}')],
                                 ['ost-0001', 'ost-0002'])

    def test_resume_gives_the_finished_outcomes_back(self):
        cases = [case_for(case_id='ost-0001'), case_for(case_id='ost-0002', label=2)]
        engine = B0Engine(prediction=make_prediction(0, [(2, QUOTE)]))
        first = self.run_arm('regression-b0', cases, engine)
        again = eval_runner.run_campaign('regression-b0', cases, build_arm=eval_runner.make_arm(
            'regression-b0', wiring(B0Engine())), max_requests=24, out_dir=self.tmp / 'regression-b0', resume=True)
        self.assertEqual(again, first)
        self.assertEqual([run.label for run in again], [0, 2])

    def test_regression_arms_refuse_other_cases_and_the_other_arms_refuse_regression_cases(self):
        with self.assertRaises(ValueError):
            eval_runner.run_campaign('regression-b0', [object()], build_arm=lambda case, budget: None,
                                     max_requests=5, out_dir=self.tmp / 'a')
        with self.assertRaises(ValueError):
            eval_runner.run_campaign('b0', [case_for()], build_arm=lambda case, budget: None, max_requests=5,
                                     out_dir=self.tmp / 'b')
        self.assertFalse((self.tmp / 'a').exists() or (self.tmp / 'b').exists())


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class CompareTests(unittest.TestCase):
    def pairs(self, rows):
        """rows: (id, label, b0 outcome, agent outcome) -> the two lists compare_paired takes."""
        return ([outcome(i, b0, label) for i, label, b0, _ in rows],
                [outcome(i, agent, label, arm='regression-agent') for i, label, _, agent in rows])

    def test_compare_paired_counts_net_worse_and_lists_every_changed_case(self):
        b0, agent = self.pairs([
            ('a', 0, 'gestuetzt', 'gestuetzt'),  # unchanged
            ('b', 0, 'gestuetzt', 'nicht_belegt'),  # worse
            ('c', 2, 'nicht_belegt', 'widersprochen'),  # better
            ('d', 2, 'nicht_belegt', 'rueckfrage'),  # both wrong, different: changed but not better or worse
            ('e', 1, 'widersprochen', 'widersprochen'),  # unchanged, wrong in both
        ])
        result = regression.compare_paired(b0, agent)
        self.assertEqual([change.id for change in result.changed], ['b', 'c', 'd'])  # the order of the cases
        self.assertEqual([change.direction for change in result.changed],
                         [regression.WORSE, regression.BETTER, regression.EQUAL])
        self.assertEqual((result.cases, result.worse, result.better, result.net_worse), (5, 1, 1, 0))
        self.assertTrue(result.passes)
        for change in result.changed:  # every changed case comes with its reason
            self.assertTrue(change.reason)
        worse = result.changed[0]
        self.assertEqual((worse.b0, worse.agent), ('gestuetzt', 'nicht_belegt'))
        self.assertIn('gestuetzt', worse.reason)
        self.assertIn('nicht_belegt', worse.reason)

    def test_the_reason_names_the_agents_error_or_note(self):
        b0 = [outcome('a', 'gestuetzt', 0)]
        agent = [CaseOutcome(id='a', arm='regression-agent', outcome='technischer_fehler', label=0,
                             error='Anfrage-Budget erschöpft')]
        [change] = regression.compare_paired(b0, agent).changed
        self.assertIn('Anfrage-Budget erschöpft', change.reason)
        agent = [CaseOutcome(id='a', arm='regression-agent', outcome='nicht_belegt', label=0,
                             note='Urteil ohne prüfbaren Beleg verworfen')]
        [change] = regression.compare_paired(b0, agent).changed
        self.assertIn('Urteil ohne prüfbaren Beleg verworfen', change.reason)

    def test_one_net_worse_passes_two_fail(self):
        def worse(count, better=0):
            rows = [(f'w{n}', 0, 'gestuetzt', 'nicht_belegt') for n in range(count)]
            rows += [(f'b{n}', 0, 'nicht_belegt', 'gestuetzt') for n in range(better)]
            rows.append(('same', 0, 'gestuetzt', 'gestuetzt'))
            return regression.compare_paired(*self.pairs(rows))

        self.assertEqual((worse(0).net_worse, worse(0).passes), (0, True))
        self.assertEqual((worse(1).net_worse, worse(1).passes), (1, True))
        self.assertEqual((worse(2).net_worse, worse(2).passes), (2, False))
        self.assertEqual((worse(3, better=2).net_worse, worse(3, better=2).passes), (1, True))  # net, not gross
        self.assertEqual((worse(1, better=3).net_worse, worse(1, better=3).passes), (-2, True))

    def test_a_technical_error_counts_as_worse_than_a_neutral_result(self):
        # gold "gestuetzt"; B0 says nicht_belegt (wrong), the agent ends in a technical error: that is worse
        result = regression.compare_paired(*self.pairs([('a', 0, 'nicht_belegt', 'technischer_fehler')]))
        self.assertEqual([change.direction for change in result.changed], [regression.WORSE])
        self.assertEqual(result.net_worse, 1)
        # the other way round it is better
        result = regression.compare_paired(*self.pairs([('a', 0, 'technischer_fehler', 'nicht_belegt')]))
        self.assertEqual([change.direction for change in result.changed], [regression.BETTER])
        # a technical error is no less wrong than a neutral result when the gold is nicht_belegt: the neutral result
        # is the correct one there
        result = regression.compare_paired(*self.pairs([('a', 1, 'technischer_fehler', 'nicht_belegt')]))
        self.assertEqual([change.direction for change in result.changed], [regression.BETTER])
        # a case the campaign did not run is no result either: worse than any result
        result = regression.compare_paired(*self.pairs([('a', 0, 'nicht_belegt', eval_runner.NOT_RUN)]))
        self.assertEqual([change.direction for change in result.changed], [regression.WORSE])

    def test_a_correct_result_beats_a_wrong_one_whatever_the_label(self):
        for label, right, wrong in ((0, 'gestuetzt', 'widersprochen'), (1, 'nicht_belegt', 'gestuetzt'),
                                    (2, 'widersprochen', 'nicht_belegt')):
            with self.subTest(label=label):
                lost = regression.compare_paired(*self.pairs([('a', label, right, wrong)]))
                won = regression.compare_paired(*self.pairs([('a', label, wrong, right)]))
                self.assertEqual((lost.changed[0].direction, won.changed[0].direction),
                                 (regression.WORSE, regression.BETTER))

    def test_the_two_runs_must_cover_the_same_cases(self):
        b0, agent = self.pairs([('a', 0, 'gestuetzt', 'gestuetzt'), ('b', 0, 'gestuetzt', 'gestuetzt')])
        with self.assertRaises(ValueError):  # a case missing on one side is never dropped silently
            regression.compare_paired(b0, agent[:1])
        with self.assertRaises(ValueError):
            regression.compare_paired(b0 + b0[:1], agent + agent[:1])
        mislabeled = [agent[0], outcome('b', 'gestuetzt', 2, arm='regression-agent')]
        with self.assertRaises(ValueError):  # the same case has the same gold label on both sides
            regression.compare_paired(b0, mislabeled)


@unittest.skipUnless(HAS_LANGGRAPH, 'Install requirements-agent.txt')
class CommandLineTests(unittest.TestCase):
    def main(self, *args):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import run_agent_eval
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = run_agent_eval.main(list(args))
        return code, stderr.getvalue()

    def test_the_regression_split_and_the_regression_arms_belong_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            for arm, split in (('b0', 'regression'), ('regression-b0', 'dev'), ('regression-agent', 'test')):
                with self.subTest(arm=arm, split=split):
                    out = Path(tmp) / f'{arm}-{split}'
                    code, message = self.main('--arm', arm, '--split', split, '--max-requests', '5', '--mock',
                                              '--out', str(out))
                    self.assertEqual(code, 1)
                    self.assertTrue(message)
                    self.assertFalse(out.exists())  # refused before anything ran


if __name__ == '__main__':
    unittest.main()
