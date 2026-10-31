"""Offline checks of fair budgets, metrics and strict inference gating."""
import argparse
from pathlib import Path
from unittest.mock import patch
import pytest
from src.rag_evaluation import budget_hits, reference_metrics, run


class CharacterTokenizer:
    def __call__(self, text, **kwargs):
        return {'offset_mapping': [(i, i+1) for i in range(len(text))]}
    def encode(self, text, **kwargs):
        return list(text)


def test_budget_is_verbatim_and_shared_across_units():
    hits = [{'text': 'abc', 'page_number': 4}, {'text': 'defghi', 'page_number': 5}]
    selected = budget_hits(hits, CharacterTokenizer(), 5)
    assert [p['text'] for p in selected] == ['abc', 'de']
    assert selected[1]['page_number'] == 5
    assert hits[1]['text'] == 'defghi'
    assert budget_hits(hits, CharacterTokenizer(), 0) == []


def test_metrics_never_treat_token_coverage_as_evidence_recall():
    result = reference_metrics([{'text': 'abc'}], 'abcdef', CharacterTokenizer(), 5)
    assert result['reference_token_coverage'] == .5
    assert 'retrieval_recall_at_k' not in result


def test_real_nli_refuses_mock_before_loading_embeddings(tmp_path):
    args = argparse.Namespace(dataset=Path(__file__).parents[1]/'data/demo_dataset.jsonl', limit=1, nli=True)
    with patch('src.rag_evaluation.ApertusClient') as client, patch('src.rag_evaluation.E5Encoder') as encoder:
        client.return_value.mock = True
        with pytest.raises(ValueError, match='mock active'):
            run(args)
        encoder.assert_not_called()


def test_evidence_score_uses_gold_non_neutral_denominator():
    from src.rag_evaluation import evidence_score
    assert evidence_score([{'gold': 0, 'evidence_hit_at_5': True},
                           {'gold': 2, 'evidence_hit_at_5': False},
                           {'gold': 1, 'evidence_hit_at_5': False}]) == .5
    assert evidence_score([{'gold': 1, 'evidence_hit_at_5': False}]) is None


def test_ngram_diagnostic_rejects_tiny_clipped_overlap_and_cross_passage_joins():
    from src.rag_evaluation import lexical_overlap
    reference = 'one two three four five six seven'
    assert lexical_overlap([{'text': 'one'}], reference)['reference_5gram_recall'] == 0
    assert lexical_overlap([{'text': 'one two'}, {'text': 'three four five'}], reference)['reference_5gram_recall'] == 0
    assert lexical_overlap([{'text': reference}], reference)['reference_5gram_recall'] == 1


def test_gold_validation_distinguishes_unknown_and_reviewed_empty():
    from src.rag_evaluation import validate_gold_pages
    assert validate_gold_pages(None) is None
    assert validate_gold_pages([]) == []
    assert validate_gold_pages([2, 2, 4]) == [2, 2, 4]
    for invalid in (True, '2', 2, {}, [True], [0], [-2], [1.5]):
        with pytest.raises(ValueError):
            validate_gold_pages(invalid)


def test_context_metric_includes_later_baseline_passages_and_restores_config(tmp_path):
    import json
    from types import SimpleNamespace
    from src import config
    pdf = tmp_path/'source.pdf'
    pdf.write_bytes(b'booklet')
    reference = 'evidence matches this exact reference with several meaningful words'
    hits = [{'text': 'unrelated introductory content completely different here', 'page_number': 1},
            {'text': reference, 'page_number': 2}]
    dataset = tmp_path/'cases.jsonl'
    dataset.write_text('{}\n')
    rec = {'id': 'one', 'claim': 'some claim', 'booklet_pdf': pdf, 'reference_string': reference,
           'claim_language': 'de', 'reference_language': 'fr', 'vote': None, 'label': 0}
    args = argparse.Namespace(dataset=dataset, limit=None, nli=False, revision='test',
        index_dir=tmp_path/'index', top_k=1, budget=10, output=tmp_path/'report.json', save_hits=True)
    old = (config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM)
    with patch('src.rag_evaluation.load_records', return_value=[rec]), \
         patch('src.rag_evaluation.E5Encoder') as encoder, \
         patch('src.rag_evaluation.ClaimVerificationEngine') as engine, \
         patch('src.rag_evaluation.VectorRetriever') as vector, \
         patch('src.rag_evaluation.PassageRetriever') as lexical:
        encoder.return_value.model.tokenizer = CharacterTokenizer()
        encoder.return_value.identity = {'model': 'test'}
        engine.return_value._get_booklet_data.return_value = {'paragraphs': hits, 'pages': hits}
        engine.return_value.verify_claim.return_value = SimpleNamespace(passages=hits)
        vector.return_value.metadata = {'chunks': 2}
        vector.return_value.retrieve.return_value = hits
        vector.return_value.bm25.retrieve_hybrid.return_value = hits
        lexical.return_value.retrieve_hybrid.return_value = hits
        run(args)
        vector.return_value.close.assert_called_once()
        engine.return_value.close.assert_called_once()
    report = json.loads(args.output.read_text())
    native = next(r for r in report['rows'] if r['variant']=='baseline_native')
    assert native['context_reference_hit'] is True
    assert native['reference_hit_at_k'] is False
    assert native['crosslingual'] is True
    assert native['context_order']=='production_context_order'
    assert len(native['context_hits'])==2
    assert all(r['context_e5_tokens'] <= 10 for r in report['rows'] if r['variant']!='baseline_native')
    assert (config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM)==old


def test_metadata_failure_restores_config_and_closes_engine():
    from src import config
    old = (config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM)
    args = argparse.Namespace(dataset=Path(__file__).parents[1]/'data/demo_dataset.jsonl',
                              limit=1, nli=False, revision='test')
    with patch('src.rag_evaluation.E5Encoder'), patch('src.rag_evaluation.ClaimVerificationEngine') as engine, \
         patch('src.rag_evaluation.subprocess.check_output', side_effect=OSError('git unavailable')):
        with pytest.raises(OSError, match='git unavailable'):
            run(args)
        engine.return_value.close.assert_called_once()
    assert (config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM)==old


def test_explicit_subset_rejects_unknown_ids_before_model_loading():
    args=argparse.Namespace(dataset=Path(__file__).parents[1]/'data/demo_dataset.jsonl',case_ids=['unknown'])
    with patch('src.rag_evaluation.E5Encoder') as encoder:
        with pytest.raises(ValueError,match='case ids'):
            run(args)
        encoder.assert_not_called()
