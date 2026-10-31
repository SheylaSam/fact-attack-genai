"""Real Qdrant, injected semantic embeddings: no model download or API required."""
import importlib.util
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.vector_rag import VectorRetriever
from src.retriever import PassageRetriever

class Encoder:
    identity = {'model': 'test-space', 'revision': '1'}
    def chunks(self, text):
        yield text
    def encode(self, texts, query=False):
        return [[1., 0.] if any(w in t.lower() for w in ('loyer', 'miete')) else [0., 1.] for t in texts]

@unittest.skipUnless(importlib.util.find_spec('qdrant_client'), 'Install requirements-rag.txt')
class VectorRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.paras = [
            {'text': 'Mieten und Wohnungsschutz für Familien', 'page_number': 4, 'proposal_id': 1, 'section': 'COM'},
            {'text': 'Militärdienst und Bevölkerung in der Schweiz', 'page_number': 9, 'proposal_id': 2, 'section': 'FC'},
        ]
    def test_cross_language_source_filter_and_speaker(self):
        r = VectorRetriever(self.paras, encoder=Encoder())
        try:
            hit = r.retrieve('Les loyers sont élevés', top_k=1)[0]
            self.assertEqual(hit['page_number'], 4)
            self.assertEqual(hit['text'], self.paras[0]['text'])
            self.assertEqual(r.retrieve('loyer', top_k=1, exclude_sections={'COM'})[0]['page_number'], 9)
            self.assertEqual(len(r.retrieve('loyer', top_k=1, mode='vector_hybrid')), 1)
            self.assertEqual(r.retrieve('loyer', top_k=1, ensure_sections={'FC': 1})[0]['section'], 'FC')
        finally:
            r.close()
    def test_persistence_and_content_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            r = VectorRetriever(self.paras, encoder=Encoder(), index_dir=directory)
            old = r.fingerprint
            r.close()
            encoder = Encoder()
            with patch.object(encoder, 'encode', side_effect=AssertionError('Re-embedding')):
                r = VectorRetriever(self.paras, encoder=encoder, index_dir=directory)
                self.assertEqual(r.fingerprint, old)
                r.close()
            changed = [{**p, 'page_number': p['page_number'] + 1} for p in self.paras]
            r = VectorRetriever(changed, encoder=Encoder(), index_dir=directory)
            self.assertNotEqual(r.fingerprint, old)
            self.assertEqual(r.retrieve('loyer', top_k=1)[0]['page_number'], 5)
            r.close()
    def test_invalid_vectors_and_empty_corpus(self):
        for vectors in ([[math.nan, 1.]], [[0., 0.]], [[1.], [1., 2.]]):
            with self.assertRaises(ValueError):
                VectorRetriever._validate_vectors(vectors, len(vectors))
        r = VectorRetriever([], encoder=Encoder())
        try:
            self.assertEqual(r.retrieve('claim'), [])
            with self.assertRaises(ValueError):
                r.retrieve('claim', top_k=0)
        finally:
            r.close()
    def test_engine_routes_to_vector_not_part_heuristic(self):
        from src.apertus_client import ApertusClient
        from src.inference import ClaimVerificationEngine
        engine = ClaimVerificationEngine(strategy='vector', apertus_client=ApertusClient(mock=True))
        data = {'paragraphs': self.paras, 'pages': self.paras, 'full_text': 'text', 'retriever': PassageRetriever(self.paras)}
        r = VectorRetriever(self.paras, encoder=Encoder())
        try:
            with patch.object(engine, '_ready_booklet_data', return_value=data), \
                 patch.object(engine, '_part_passages', side_effect=AssertionError('Part routing')), \
                 patch('src.vector_rag.VectorRetriever', return_value=r), \
                 patch('src.config.EVIDENCE_POLICY', 'legacy'):
                prediction = engine.verify_claim('loyer', 'unused.pdf', top_k=1)
            self.assertEqual(prediction.strategy, 'vector')
            self.assertIsNone(prediction.error)
            self.assertEqual(prediction.context_pages, [4])
        finally:
            r.close()
