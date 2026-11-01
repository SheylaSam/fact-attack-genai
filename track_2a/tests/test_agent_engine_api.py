"""Public engine methods the agent layer builds on: passage selection, verification with given passages, layout."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import config
from src.apertus_client import ApertusClient, NLIOutput
from src.inference import ClaimVerificationEngine

BOOKLET = config.BOOKLETS_DIR / '2026-06-14_de.pdf'
VOTE = 'Volksinitiative «Keine 10-Millionen-Schweiz! (Nachhaltigkeitsinitiative)»'
CLAIM = 'Die Initiative will die Bevölkerung auf 10 Millionen begrenzen.'
PART_CLAIM = 'Laut der Zusammenfassung will die Initiative die Bevölkerung auf 10 Millionen begrenzen.'


class RecordingClient(ApertusClient):
    """Network-free client that records the passage texts each NLI call receives."""
    def __init__(self):
        super().__init__(mock=True)
        self.calls = []

    def infer(self, context, claim, claim_language=None, passages=None, task_a=False, label_only=False):
        self.calls.append(list(passages or []))
        return NLIOutput(label=0, reasoning='', evidence_ids=[1], tokens_prompt=10, tokens_completion=2,
                         tokens_total=12, latency_ms=1.0)

    def infer_compact(self, passages, claim, claim_language=None, vote=None, **kwargs):
        self.calls.append(list(passages))
        return NLIOutput(label=0, reasoning='', evidence_ids=[1], tokens_prompt=10, tokens_completion=2,
                         tokens_total=12, latency_ms=1.0)


@unittest.skipUnless(BOOKLET.exists(), f'Booklet not found at {BOOKLET}')
class AgentEngineApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
        # parse up front: both calls of a comparison then see the same (fully parsed) booklet
        self.engine._get_booklet_data(BOOKLET)

    def test_retrieve_passages_matches_verify_claim(self):
        for claim in (CLAIM, PART_CLAIM):  # regular hybrid retrieval and the part-context path
            with self.subTest(claim=claim):
                passages = self.engine.retrieve_passages(claim, BOOKLET, vote=VOTE, claim_language='de')
                result = self.engine.verify_claim(claim=claim, booklet_pdf=BOOKLET, claim_language='de',
                                                  strategy='hybrid', vote=VOTE)
                self.assertTrue(passages)
                self.assertEqual([p['page_number'] for p in passages], result.context_pages)

    def test_verify_with_passages_uses_only_given_passages(self):
        found = self.engine.retrieve_passages(CLAIM, BOOKLET, vote=VOTE, claim_language='de')
        given = [found[2], found[0]]  # not in retrieval order: the given order is kept
        client = RecordingClient()
        self.engine.client = client
        result = self.engine.verify_with_passages(CLAIM, BOOKLET, given, vote=VOTE, claim_language='de',
                                                  case_id='agent-1')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(client.calls[0]), 2)
        for text, passage in zip(client.calls[0], given):
            self.assertIn(passage['text'], text)
        self.assertEqual(result.context_pages, [p['page_number'] for p in given])
        self.assertEqual(result.strategy, 'agent')
        self.assertEqual(result.id, 'agent-1')
        self.assertEqual(result.label, 0)

    def test_verify_with_passages_rejects_empty(self):
        with self.assertRaises(ValueError):
            self.engine.verify_with_passages(CLAIM, BOOKLET, [], vote=VOTE)

    def test_verify_with_passages_rejects_json_prompt_mode(self):
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True), prompt_mode='json')
        passages = self.engine.retrieve_passages(CLAIM, BOOKLET, vote=VOTE, claim_language='de')
        with self.assertRaises(ValueError):
            engine.verify_with_passages(CLAIM, BOOKLET, passages[:1], vote=VOTE)

    def test_booklet_layout_returns_parts(self):
        paragraphs, parts = self.engine.booklet_layout(BOOKLET)
        self.assertTrue(paragraphs)
        self.assertIsNotNone(parts)
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            self.assertIn('title', part)
            self.assertIn('parts', part)

    def test_source_page_text_unknown_page_is_none(self):
        self.assertIsNone(self.engine.source_page_text(BOOKLET, 999))
        self.assertIsNone(self.engine.source_page_text(BOOKLET, 0))
        stored = self.engine._get_booklet_data(BOOKLET)['source_pages']
        self.assertEqual(self.engine.source_page_text(BOOKLET, 1), stored['1']['text'])


    def test_source_page_is_text_and_blocks_of_the_stored_page(self):
        stored = self.engine._get_booklet_data(BOOKLET)['source_pages']
        for number in (1, 6, 26):
            with self.subTest(page=number):
                page = self.engine.source_page(BOOKLET, number)
                self.assertEqual(page, {'text': stored[str(number)]['text'], 'blocks': stored[str(number)]['blocks']})
                self.assertEqual(page['text'], self.engine.source_page_text(BOOKLET, number))
        page = self.engine.source_page(BOOKLET, 1)
        page['blocks'].clear()  # a copy: the engine's shared cache stays intact
        self.assertTrue(stored['1']['blocks'])

    def test_source_page_unknown_page_is_none(self):
        for number in (999, 0, -1, None, '3', 2.0):
            with self.subTest(page=number):
                self.assertIsNone(self.engine.source_page(BOOKLET, number))

    def test_source_page_falls_back_to_the_pdf_without_stored_source_pages(self):
        stored = self.engine._get_booklet_data(BOOKLET)['source_pages']
        stripped = {**self.engine._get_booklet_data(BOOKLET), 'source_pages': {}}  # their extraction failed
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
        with patch.object(engine, '_ready_booklet_data', return_value=stripped), \
                patch.object(engine, '_get_booklet_data', return_value=stripped):
            self.assertIsNone(engine.source_page_text(BOOKLET, 26))
            self.assertEqual(engine.source_page(BOOKLET, 26), stored['26'])
            self.assertIsNone(engine.source_page(BOOKLET, 999))

    def test_source_page_reads_the_quick_booklet_while_the_parse_is_not_ready(self):
        stored = self.engine._get_booklet_data(BOOKLET)['source_pages']
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
        with patch.object(config, 'QUICK_PARTS', True), \
                patch.object(engine, '_ready_booklet_data', return_value=None), \
                patch.object(engine, '_get_booklet_data', side_effect=AssertionError('full parse awaited')):
            self.assertEqual(engine.source_page(BOOKLET, 26), stored['26'])


if __name__ == '__main__':
    unittest.main()
