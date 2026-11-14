"""Prepare observed June dev cases from unchanged public OST labels; no new split."""
import argparse
import hashlib
import json
from pathlib import Path
import re

SOURCE_URL = 'https://huggingface.co/api/datasets/OSTswiss/MNLIoverSwissVotingBooklets/parquet/default/train/0.parquet'
SOURCE_SHA256 = '444fe113115a1fa4134664f67ce572cb04b170b443bc741ae546a34c2f76b7c7'


def prepare(source, output, expected_sha256=SOURCE_SHA256):
    import pandas as pd
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash != expected_sha256:
        raise ValueError('Source hash mismatch: review upstream data before accepting a new snapshot')
    records = []
    for index, row in pd.read_parquet(source).iterrows():
        if not re.search(r'/2026-06-14_[^/]+\.pdf$', row['booklet_url']):
            continue
        label = int(row['entailment_label'])
        if label not in (0, 1, 2) or row['reference_language'] not in ('de', 'fr', 'it') or row['claim_language'] not in ('de', 'fr', 'it'):
            raise ValueError('Unexpected label/language')
        records.append({'id': f'ost-{index:04d}', 'claim': row['claim'],
            'claim_language': row['claim_language'], 'reference_string': row['reference_string'],
            'reference_language': row['reference_language'], 'entailment_label': label,
            'vote': row['vote'], 'booklet_file': f"2026-06-14_{row['reference_language']}.pdf",
            'booklet_publish_date': str(row['booklet_publish_date'])[:10], 'booklet_url': row['booklet_url'],
            'data_role': 'observed_dev_regression',
            'leakage_group': 'booklet-date:2026-06-14',
            'label_source': 'OST public parquet, unchanged', 'source_row_index': int(index)})
    if not records:
        raise ValueError('No matching June booklet cases')
    content = ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in records)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content)
    pairs = {}
    for r in records:
        key = f"{r['claim_language']}->{r['reference_language']}"
        pairs[key] = pairs.get(key, 0)+1
    manifest = {'data_role': 'observed_dev_regression', 'source_url': SOURCE_URL,
        'source_sha256': source_hash, 'output_sha256': hashlib.sha256(content.encode()).hexdigest(),
        'selection': 'All source rows with booklet URL 2026-06-14; no outcome-based selection',
        'case_count': len(records), 'by_pair': dict(sorted(pairs.items())),
        'crosslingual_count': sum(r['claim_language'] != r['reference_language'] for r in records),
        'labels': {str(k): sum(r['entailment_label']==k for r in records) for k in (0, 1, 2)},
        'gold_page_annotations': 0, 'new_independent_test_data': False}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parquet', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/genai/ost_june_dev.jsonl'))
    args = parser.parse_args()
    print(json.dumps(prepare(args.parquet, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
