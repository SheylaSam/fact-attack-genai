"""SourceIndex: real Qdrant, injected semantic embeddings and layouts; no model, PDF or API required."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HAS_QDRANT = importlib.util.find_spec('qdrant_client') is not None
if HAS_QDRANT:
    from src.agent.source_index import SourceIndex, VoteCandidate, VoteRef

RENT = 'miet'


class Encoder:
    identity = {'model': 'test-space', 'revision': '1'}

    def chunks(self, text):
        yield text

    def encode(self, texts, query=False):
        return [[1., 0.] if RENT in t.lower() else [0., 1.] for t in texts]


def para(text, page, section=None):
    return {'text': text, 'page_number': page, 'section': section}


def vote(title, pages, names=None):
    return {'title': title, 'names': names or [title], 'parts': {'summary': pages[:1], 'arguments': pages[1:]},
            'speakers': None}


def entry(date, language, votes, sha):
    return {'date': date, 'language': language, 'url': f'https://www.bk.admin.ch/{date}_{language}.pdf',
            'votes': votes, 'sha256': sha}


TITLE_SUBLET = 'Änderung des Obligationenrechts (Mietrecht: Untermiete)'
TITLE_EVICTION = 'Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs)'
TITLE_CIVIL_SERVICE = 'Änderung des Zivildienstgesetzes'

# 2024-11-24: page 1 is the contents page (no vote), 2-3 the sublet vote, 4-5 the eviction vote
RENT_BOOKLET = ([
    para('Inhalt Untermiete Kündigung Eigenbedarf', 1, 'COM'),
    para('Die Untermiete wird neu geregelt: Mieter brauchen die Zustimmung der Vermieter.', 2, 'SUMMARY'),
    para('Wer untervermietet, muss die Miete offenlegen.', 3, 'COM'),
    para('Bei Eigenbedarf darf der Vermieter die Wohnung leichter kündigen, die Miete bleibt gleich.', 4, 'SUMMARY'),
    para('Gegen die Kündigung wegen Eigenbedarfs wehren sich die Mieter.', 5, 'FC'),
    para('Der Nationalstrassenbau wird ausgebaut.', 6, 'SUMMARY'),
], [
    vote('Untermiete', [2, 3], names=['Untermiete', TITLE_SUBLET]),
    vote('Kündigung Eigenbedarf', [4, 5], names=['Kündigung Eigenbedarf', TITLE_EVICTION]),
])
CIVIL_SERVICE_BOOKLET = ([
    para('Der Zivildienst wird für Zivis strenger geregelt.', 2, 'SUMMARY'),
    para('Das Parlament will weniger Wechsel vom Militär in den Zivildienst.', 3, 'FC'),
], [vote('Zivildienstgesetz', [2, 3], names=['Zivildienstgesetz', TITLE_CIVIL_SERVICE])])
NO_TOC_BOOKLET = ([
    para('Ohne Inhaltsverzeichnis: Seite eins.', 1),
    para('Ohne Inhaltsverzeichnis: Seite zwei zur Miete.', 2),
], None)
LAYOUTS = {
    '2024-11-24_de.pdf': RENT_BOOKLET,
    '2026-06-14_de.pdf': CIVIL_SERVICE_BOOKLET,
    '2024-11-24_fr.pdf': ([para('Les loyers augmentent.', 2)], [vote('Sous-location', [2])]),
    '2030-01-01_de.pdf': NO_TOC_BOOKLET,
}
CATALOG = {
    '2024-11-24_de.pdf': entry('2024-11-24', 'de', [TITLE_EVICTION, TITLE_SUBLET, 'Nationalstrassen'], 'sha-rent'),
    '2024-11-24_fr.pdf': entry('2024-11-24', 'fr', ['Sous-location'], 'sha-rent-fr'),
    '2026-06-14_de.pdf': entry('2026-06-14', 'de', [TITLE_CIVIL_SERVICE], 'sha-civil'),
}


@unittest.skipUnless(HAS_QDRANT, 'Install requirements-rag.txt')
class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.booklets = self.root / 'booklets'
        self.booklets.mkdir()
        for name in LAYOUTS:  # placeholders: the injected layout supplies the content
            (self.booklets / name).write_bytes(b'%PDF-1.7 placeholder')
        self.layout_calls = []
        self.indexes = []

    def layout(self, path):
        self.layout_calls.append(Path(path).name)
        return LAYOUTS[Path(path).name]

    def build(self, sources=None, **kwargs):
        index = SourceIndex(CATALOG if sources is None else sources, self.booklets, encoder=kwargs.pop('encoder', Encoder()),
                            layout=kwargs.pop('layout', self.layout), **kwargs)
        self.indexes.append(index)
        self.addCleanup(index.close)
        return index

    def test_search_votes_returns_both_rent_votes(self):
        index = self.build()
        found = index.search_votes('Die Mietrechtsvorlage stärkt die Vermieter.', k=2)
        self.assertEqual({c.vote.vote_id for c in found}, {'2024-11-24_de#1', '2024-11-24_de#2'})
        self.assertTrue(all(isinstance(c, VoteCandidate) and c.hits >= 1 for c in found))

    def test_vote_candidates_aggregate_chunk_hits(self):
        index = self.build()
        found = index.search_votes('Miete', k=5)
        top = found[0]
        self.assertAlmostEqual(top.score, 1.0)
        # the two rent chunks of the sublet vote (pages 2, 3) and the two of the eviction vote (pages 4, 5)
        self.assertEqual({c.vote.vote_id: c.hits for c in found if c.score > 0.5}, {'2024-11-24_de#1': 2,
                                                                                   '2024-11-24_de#2': 2})
        self.assertEqual(top.vote.vote_id, '2024-11-24_de#1')  # tie on score and hits: smaller vote_id first
        self.assertEqual(top.best_page, 2)  # best chunk of the tie: the lowest point id, the first indexed
        self.assertEqual(top.snippet, RENT_BOOKLET[0][1]['text'])
        scores = [c.score for c in found]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_equal_scores_rank_by_hits_then_vote_id(self):
        paragraphs = [para(f'Miete {name}', page) for name, page in zip('ABCD', (2, 3, 4, 5))]
        # vote #2 is indexed before vote #1 (earlier page), vote #3 has twice the chunks of the others
        votes = [vote('Eins', [3]), vote('Zwei', [2]), vote('Drei', [4, 5])]
        layouts = {**LAYOUTS, '2024-11-24_de.pdf': (paragraphs, votes)}
        index = self.build(layout=lambda path: layouts[Path(path).name])
        found = [c for c in index.search_votes('Miete', k=5) if c.score > 0.5]
        self.assertEqual([(c.vote.vote_id, c.hits) for c in found],
                         [('2024-11-24_de#3', 2), ('2024-11-24_de#1', 1), ('2024-11-24_de#2', 1)])

    def test_snippet_is_at_most_200_characters(self):
        long_text = 'Miete ' + 'sehr lang ' * 100
        layouts = {**LAYOUTS, '2024-11-24_de.pdf': ([para(long_text, 2)], [vote('Untermiete', [2])])}
        index = self.build(layout=lambda path: layouts[Path(path).name])
        found = index.search_votes('Miete', k=1)
        self.assertLessEqual(len(found[0].snippet), 200)
        self.assertTrue(long_text.startswith(found[0].snippet))

    def test_search_votes_k_limits_the_result(self):
        index = self.build()
        self.assertEqual(len(index.search_votes('Miete', k=1)), 1)
        with self.assertRaises(ValueError):
            index.search_votes('Miete', k=0)

    def test_search_passages_stays_within_vote_pages(self):
        index = self.build()
        for vote_id, ref in index.votes.items():
            passages = index.search_passages(vote_id, 'Miete', k=8)
            self.assertTrue(passages, vote_id)
            for passage in passages:
                self.assertIn(passage['page_number'], ref.pages)
                self.assertEqual(passage['vote_id'], vote_id)
                self.assertIn('text', passage)
                self.assertIn('section', passage)

    def test_search_passages_returns_original_text_ranked_by_similarity(self):
        index = self.build()
        passages = index.search_passages('2024-11-24_de#2', 'Mieter', k=2)
        self.assertEqual([p['page_number'] for p in passages], [4, 5])
        self.assertEqual(passages[0]['text'], RENT_BOOKLET[0][3]['text'])
        self.assertEqual(passages[0]['section'], 'SUMMARY')
        self.assertEqual(len(index.search_passages('2024-11-24_de#2', 'Mieter', k=1)), 1)

    def test_unknown_vote_id_raises(self):
        index = self.build()
        for vote_id in ('2024-11-24_de#7', '../../.env', '', None, ['2024-11-24_de#1']):
            with self.subTest(vote_id=vote_id), self.assertRaises(ValueError):
                index.search_passages(vote_id, 'Miete')

    def test_vote_ids_titles_and_pages(self):
        index = self.build()
        first, second = index.votes['2024-11-24_de#1'], index.votes['2024-11-24_de#2']
        self.assertIsInstance(first, VoteRef)
        self.assertEqual((first.source_id, first.date, first.language), ('2024-11-24_de', '2024-11-24', 'de'))
        # k is 1-based in the order of the booklet's parts; the title is the catalog title match_vote assigns
        self.assertEqual(first.title, TITLE_SUBLET)
        self.assertEqual(second.title, TITLE_EVICTION)
        self.assertEqual(first.pages, (2, 3))
        self.assertEqual(second.pages, (4, 5))
        self.assertEqual(list(index.votes), ['2024-11-24_de#1', '2024-11-24_de#2', '2026-06-14_de#1'])

    def test_title_falls_back_to_contents_title(self):
        catalog = {'2024-11-24_de.pdf': entry('2024-11-24', 'de', ['Nationalstrassen'], 'sha-rent')}
        index = self.build(catalog)
        self.assertEqual(index.votes['2024-11-24_de#1'].title, 'Untermiete')
        self.assertEqual(index.votes['2024-11-24_de#2'].title, 'Kündigung Eigenbedarf')

    def test_pages_without_vote_are_not_indexed(self):
        index = self.build()
        # pages 1 (contents) and 6 of the rent booklet belong to no vote: 4 of its 6 paragraphs, plus 2 civil service
        self.assertEqual(index.metadata['chunks'], 6)
        for vote_id in index.votes:
            texts = [p['text'] for p in index.search_passages(vote_id, 'Miete', k=50)]
            self.assertNotIn(RENT_BOOKLET[0][0]['text'], texts)
            self.assertNotIn(RENT_BOOKLET[0][5]['text'], texts)

    def test_booklet_without_parts_gets_whole_booklet_vote(self):
        catalog = {'2030-01-01_de.pdf': entry('2030-01-01', 'de', ['Irgendeine Vorlage'], 'sha-none')}
        index = self.build(catalog)
        self.assertEqual(list(index.votes), ['2030-01-01_de#0'])
        whole = index.votes['2030-01-01_de#0']
        self.assertEqual(whole.title, 'Gesamtes Büchlein')
        self.assertEqual(whole.pages, (1, 2))
        self.assertEqual(index.search_votes('Miete')[0].vote.vote_id, '2030-01-01_de#0')

    def test_missing_pdf_is_skipped(self):
        catalog = {**CATALOG, '2099-01-01_de.pdf': entry('2099-01-01', 'de', ['Zukunft'], 'sha-future')}
        index = self.build(catalog)
        self.assertEqual(index.skipped, ['2099-01-01_de.pdf'])
        self.assertNotIn('2099-01-01_de.pdf', self.layout_calls)  # R1: checked before layout is called
        self.assertEqual(len(index.votes), 3)

    def test_invalid_pdf_is_skipped(self):
        (self.booklets / '2026-06-14_de.pdf').write_bytes(b'<html>not a pdf</html>')
        index = self.build()
        self.assertEqual(index.skipped, ['2026-06-14_de.pdf'])
        self.assertNotIn('2026-06-14_de.pdf', self.layout_calls)
        self.assertEqual({v.source_id for v in index.votes.values()}, {'2024-11-24_de'})

    def test_only_requested_languages_indexed(self):
        index = self.build()
        self.assertFalse([v for v in index.votes.values() if v.language == 'fr'])
        self.assertFalse([v for v in index.votes if '_fr' in v])
        self.assertNotIn('2024-11-24_fr.pdf', self.layout_calls)
        self.assertEqual(index.skipped, [])  # a language that was not requested is not "skipped"
        both = self.build(languages=('de', 'fr'))
        self.assertIn('2024-11-24_fr#1', both.votes)

    def test_persistent_index_is_reused(self):
        index_dir = self.root / 'index'
        first = self.build(index_dir=index_dir)
        self.assertIs(first.metadata['index_reused'], False)
        votes, fingerprint = dict(first.votes), first.metadata['index_fingerprint']
        first.close()
        encoder = Encoder()
        with patch.object(encoder, 'encode', side_effect=AssertionError('Re-embedding')):
            second = self.build(index_dir=index_dir, encoder=encoder, layout=self.fail_layout)
        self.assertIs(second.metadata['index_reused'], True)
        self.assertEqual(second.metadata['index_fingerprint'], fingerprint)
        self.assertEqual(second.votes, votes)
        self.assertEqual({c.vote.vote_id for c in second.search_votes('Miete', k=2)},
                         {'2024-11-24_de#1', '2024-11-24_de#2'})
        self.assertEqual(second.search_passages('2026-06-14_de#1', 'Zivildienst', k=1)[0]['page_number'], 2)

    def fail_layout(self, path):
        raise AssertionError('A reused index must not parse the booklets again')

    def test_changed_catalog_hash_or_languages_build_a_new_index(self):
        index_dir = self.root / 'index'
        first = self.build(index_dir=index_dir)
        fingerprint = first.metadata['index_fingerprint']
        first.close()
        changed = {**CATALOG, '2026-06-14_de.pdf': entry('2026-06-14', 'de', [TITLE_CIVIL_SERVICE], 'sha-other')}
        second = self.build(changed, index_dir=index_dir)
        self.assertNotEqual(second.metadata['index_fingerprint'], fingerprint)
        self.assertIs(second.metadata['index_reused'], False)
        second.close()
        third = self.build(index_dir=index_dir, languages=('de', 'fr'))
        self.assertNotEqual(third.metadata['index_fingerprint'], fingerprint)
        third.close()
        self.assertEqual(len(list(index_dir.iterdir())), 3)

    def test_skipped_pdf_changes_the_fingerprint_once_it_appears(self):
        index_dir = self.root / 'index'
        (self.booklets / '2026-06-14_de.pdf').write_bytes(b'broken')
        first = self.build(index_dir=index_dir)
        self.assertEqual(first.skipped, ['2026-06-14_de.pdf'])
        first.close()
        (self.booklets / '2026-06-14_de.pdf').write_bytes(b'%PDF-1.7 placeholder')
        second = self.build(index_dir=index_dir)
        self.assertIs(second.metadata['index_reused'], False)
        self.assertEqual(second.skipped, [])
        self.assertIn('2026-06-14_de#1', second.votes)

    def test_unfinished_build_is_rebuilt_and_inconsistent_index_is_refused(self):
        index_dir = self.root / 'index'
        first = self.build(index_dir=index_dir)
        directory = index_dir / first.metadata['index_fingerprint']
        first.close()
        manifest = directory / 'votes.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        manifest.write_text(json.dumps({**data, 'chunks': data['chunks'] + 1}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Incomplete index'):
            self.build(index_dir=index_dir)
        manifest.unlink()  # a build that stopped before the manifest was written
        again = self.build(index_dir=index_dir)
        self.assertIs(again.metadata['index_reused'], False)
        self.assertEqual(len(again.search_votes('Miete', k=2)), 2)

    def test_no_available_booklet_gives_an_empty_index(self):
        for path in self.booklets.iterdir():
            path.unlink()
        index = self.build()
        self.assertEqual(index.votes, {})
        self.assertEqual(sorted(index.skipped), ['2024-11-24_de.pdf', '2026-06-14_de.pdf'])
        self.assertEqual(index.search_votes('Miete'), [])
        with self.assertRaises(ValueError):
            index.search_passages('2024-11-24_de#1', 'Miete')

    def test_catalog_entry_without_hash_is_refused(self):
        catalog = {'2024-11-24_de.pdf': {k: v for k, v in CATALOG['2024-11-24_de.pdf'].items() if k != 'sha256'}}
        with self.assertRaisesRegex(ValueError, 'sha256'):
            SourceIndex(catalog, self.booklets, encoder=Encoder(), layout=self.layout)

    def test_default_layout_is_the_engines_booklet_layout(self):
        calls, engines = [], []

        class FakeEngine:
            def __init__(self, **kwargs):
                self.kwargs, self.closed = kwargs, False
                engines.append(self)

            def booklet_layout(self, path):
                calls.append(Path(path))
                return LAYOUTS[Path(path).name]

            def close(self):
                self.closed = True

        with patch('src.inference.ClaimVerificationEngine', FakeEngine):
            index = SourceIndex(CATALOG, self.booklets, encoder=Encoder())
        self.addCleanup(index.close)
        self.assertEqual(calls, [self.booklets / '2024-11-24_de.pdf', self.booklets / '2026-06-14_de.pdf'])
        self.assertEqual(len(engines), 1)
        self.assertTrue(engines[0].closed)
        self.assertTrue(engines[0].kwargs['apertus_client'].mock)  # never a paid client: the layout needs no model

    def test_metadata_reports_the_build(self):
        index = self.build()
        meta = index.metadata
        self.assertEqual((meta['votes'], meta['sources'], meta['chunks']), (3, 2, 6))
        self.assertEqual(meta['languages'], ['de'])
        self.assertEqual(meta['encoder'], Encoder.identity)
        json.dumps(meta)  # the build report stores it


if __name__ == '__main__':
    unittest.main()
