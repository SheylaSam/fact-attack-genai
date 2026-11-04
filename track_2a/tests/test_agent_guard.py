"""Guard: fixed-threshold assessment of the vote candidates (eindeutig / mehrdeutig / keine)."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent import guard, settings
from src.agent.guard import Thresholds, assess, hint, question
from src.agent.source_index import VoteCandidate, VoteRef


def vote(vote_id, title='Vorlage', date='2024-11-24'):
    return VoteRef(vote_id, vote_id.split('#')[0], date, 'de', title, (1,))


def cand(vote_id, score, hits, title='Vorlage', date='2024-11-24'):
    return VoteCandidate(vote=vote(vote_id, title, date), score=score, hits=hits, best_page=1, snippet='x')


RENT = [cand('2024-11-24_de#3', .867, 15), cand('2020-02-09_de#1', .860, 13), cand('2024-11-24_de#2', .860, 14),
        cand('2021-11-28_de#3', .848, 1), cand('2022-05-15_de#3', .845, 3)]


class GuardTests(unittest.TestCase):
    def test_close_rent_votes_are_ambiguous(self):
        a = assess(RENT, Thresholds(0.02, 0.5, None))
        self.assertEqual(a.kind, 'mehrdeutig')
        self.assertEqual(a.close, ('2024-11-24_de#3', '2020-02-09_de#1', '2024-11-24_de#2'))
        self.assertEqual(a.top, '2024-11-24_de#3')

    def test_clear_lead_is_unambiguous(self):
        a = assess([cand('2026-06-14_de#1', .892, 23), cand('2024-11-24_de#1', .855, 5)], Thresholds(0.02, 0.5, None))
        self.assertEqual(a.kind, 'eindeutig')
        self.assertEqual(a.close, ('2026-06-14_de#1',))

    def test_margin_is_strict(self):
        a = assess([cand('a_de#1', 0.75, 5), cand('b_de#1', 0.5, 5)], Thresholds(0.25, 0, None))
        self.assertEqual(a.kind, 'eindeutig')

    def test_few_hits_are_not_close(self):
        few = assess([cand('a_de#1', .9, 15), cand('b_de#1', .9, 7)], Thresholds(0.02, 0.5, None))
        enough = assess([cand('a_de#1', .9, 15), cand('b_de#1', .9, 8)], Thresholds(0.02, 0.5, None))
        self.assertEqual(few.kind, 'eindeutig')
        self.assertEqual(enough.kind, 'mehrdeutig')

    def test_close_set_is_capped_at_four(self):
        ids = [f'2024-11-24_de#{k}' for k in range(1, 7)]
        a = assess([cand(i, .9, 10) for i in ids], Thresholds(0.02, 0.5, None))
        self.assertEqual(a.kind, 'mehrdeutig')
        self.assertEqual(len(a.close), 4)
        self.assertEqual(a.close, tuple(ids[:4]))

    def test_no_candidates_is_keine(self):
        a = assess([], Thresholds(0.02, 0.5, None))
        self.assertEqual((a.kind, a.top, a.close), ('keine', None, ()))

    def test_weak_top_is_keine_with_none_below(self):
        a = assess([cand('a_de#1', .85, 10)], Thresholds(0.02, 0.5, 0.9))
        self.assertEqual(a.kind, 'keine')

    def test_question_names_titles_and_dates(self):
        options = [vote('a_de#1', 'A', '2024-11-24'), vote('b_de#1', 'B', '2020-02-09'),
                   vote('c_de#1', 'C', '2024-11-24')]
        self.assertTrue(question(options).endswith('«A» (24.11.2024), «B» (09.02.2020) oder «C» (24.11.2024)?'))
        self.assertEqual(question(options[:2]),
                         'Welche Vorlage ist gemeint: «A» (24.11.2024) oder «B» (09.02.2020)?')
        long = question([vote('a_de#1', 'T' * 200), options[1]])
        self.assertIn('…', long)
        self.assertNotIn('T' * 91, long)

    def test_hint_texts_and_json_safe_data(self):
        t = Thresholds(0.02, 0.5, None)
        rent = assess(RENT, t)
        self.assertEqual(hint(rent), 'Einschätzung: mehrdeutig, 2024-11-24_de#3, 2020-02-09_de#1, 2024-11-24_de#2 '
                         'liegen gleichauf. Lässt die Behauptung nicht erkennen, welche gemeint ist, beende mit '
                         'rueckfrage und diesen Optionen.')
        self.assertEqual(hint(assess(RENT[:1], t)), 'Einschätzung: 2024-11-24_de#3 passt klar am besten.')
        self.assertEqual(hint(assess([], t)), 'Einschätzung: keine passende Vorlage. Beende mit quelle_fehlt.')
        data = json.loads(json.dumps(rent.to_data()))
        self.assertEqual(data['kind'], 'mehrdeutig')
        self.assertEqual(data['thresholds'], {'score_margin': 0.02, 'min_hits_ratio': 0.5, 'none_below': None})

    def test_guard_defaults(self):
        self.assertEqual((settings.GUARD_SCORE_MARGIN, settings.GUARD_MIN_HITS_RATIO, settings.GUARD_NONE_BELOW),
                         (0.02, 0.5, None))
        self.assertEqual(guard.current_thresholds(), Thresholds(0.02, 0.5, None))


if __name__ == '__main__':
    unittest.main()
