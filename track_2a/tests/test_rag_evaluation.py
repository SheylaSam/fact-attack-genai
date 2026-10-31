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
