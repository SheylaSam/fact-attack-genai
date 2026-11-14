"""The dev subset preserves source claims/labels and fails on dataset drift."""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

spec = importlib.util.spec_from_file_location('prepare_rag_dev', Path(__file__).parents[1]/'scripts/prepare_rag_dev.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_source_hash_drift_fails_before_reading_parquet(tmp_path):
    source = tmp_path/'source.parquet'
    source.write_bytes(b'changed')
    with patch('pandas.read_parquet') as read:
        with pytest.raises(ValueError, match='hash mismatch'):
            module.prepare(source, tmp_path/'cases.jsonl')
        read.assert_not_called()


def test_subset_preserves_official_labels_and_language_pairs(tmp_path):
    import pandas as pd
    source = tmp_path/'source.parquet'
    source.write_bytes(b'fixture')
    rows = [{'claim': 'original claim', 'claim_language': 'it', 'reference_language': 'fr',
             'reference_string': 'original reference', 'entailment_label': 2,
             'vote': 'original vote', 'booklet_publish_date': '2026-06-14',
             'booklet_url': 'https://example.org/2026-06-14_explications.pdf'},
            {'booklet_url': 'https://example.org/2024-11-24_other.pdf'}]
    output = tmp_path/'cases.jsonl'
    with patch('pandas.read_parquet', return_value=pd.DataFrame(rows)):
        manifest = module.prepare(source, output, hashlib.sha256(source.read_bytes()).hexdigest())
    saved = json.loads(output.read_text())
    assert saved['claim']=='original claim' and saved['entailment_label']==2
    assert saved['reference_language']=='fr' and saved['booklet_file']=='2026-06-14_fr.pdf'
    assert 'gold_evidence_pages' not in saved
    assert manifest['case_count']==1 and manifest['crosslingual_count']==1
    assert manifest['new_independent_test_data'] is False
