"""Prepare UNREVIEWED per-case annotations; retrieval candidates never become gold."""
import argparse
import hashlib
import json
from pathlib import Path


def prepare(dataset, report_path, output):
    report = json.loads(report_path.read_text())
    if hashlib.sha256(dataset.read_bytes()).hexdigest()!=report['dataset_sha256']:
        raise ValueError('Dataset does not match retrieval report')
    groups = {}
    for line in dataset.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = hashlib.sha256((r['reference_language']+'\n'+r['reference_string']).encode()).hexdigest()
        group = groups.setdefault(key, dict(reference_cluster=key, booklet_file=r['booklet_file'],
            reference_language=r['reference_language'], reference_string=r['reference_string'],
            candidate_physical_pages=[], cases=[], data_role='observed_dev_regression',
            warning='Retrieval candidates are not gold. Review each claim separately; a shared reference does not imply shared evidence.'))
        group['cases'].append(dict(id=r['id'], claim=r['claim'], entailment_label=r['entailment_label'],
            gold_evidence_pages=None, review_status='unreviewed', reviewer=None, note=''))
    for row in report['rows']:
        if row['variant'] not in ('baseline_native', 'bm25_chunks', 'vector'):
            continue
        for hit in row.get('pre_budget_hits', [])[:5]:
            pages = groups[row['reference_cluster']]['candidate_physical_pages']
            if hit['page_number'] not in pages:
                pages.append(hit['page_number'])
    for group in groups.values():
        group['candidate_physical_pages'].sort()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for _, r in sorted(groups.items())))
    return len(groups)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'{prepare(args.dataset, args.report, args.output)} unreviewed reference groups')


if __name__ == '__main__':
    main()
