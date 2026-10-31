"""Paired lexical diagnostics on a single observed-dev retrieval report; no significance claim."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def paired(report, variant, baseline='bm25_chunks', group='all'):
    rows = [r for r in report['rows'] if group=='all' or
            (group=='crosslingual' and r['crosslingual']) or (group=='monolingual' and not r['crosslingual'])]
    def index(name):
        result = {}
        for row in rows:
            if row['variant']==name:
                if row['id'] in result:
                    raise ValueError('Duplicate case/variant in report')
                result[row['id']] = row
        return result
    a, b = index(variant), index(baseline)
    if not a or a.keys()!=b.keys():
        raise ValueError('Paired comparison requires identical nonempty case ids')
    deltas, clusters = [], defaultdict(list)
    for key in sorted(a):
        if a[key]['reference_cluster']!=b[key]['reference_cluster'] or a[key]['pair']!=b[key]['pair']:
            raise ValueError('Case identity metadata differs between variants')
        left, right = a[key]['reference_5gram_recall'], b[key]['reference_5gram_recall']
        if left is None or right is None:
            continue
        delta = left-right
        deltas.append(delta)
        clusters[a[key]['reference_cluster']].append(delta)
    return {'variant': variant, 'baseline': baseline, 'group': group, 'paired_case_count': len(deltas),
        'reference_cluster_count': len(clusters), 'lexical_recall_delta_mean': sum(deltas)/len(deltas) if deltas else None,
        'cluster_mean_delta': sum(sum(v)/len(v) for v in clusters.values())/len(clusters) if clusters else None,
        'higher': sum(d>1e-12 for d in deltas), 'equal': sum(abs(d)<=1e-12 for d in deltas),
        'lower': sum(d < -1e-12 for d in deltas),
        'interpretation': 'Exact reference word-5gram overlap only; descriptive observed-dev comparison, not NLI or semantic evidence quality.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    result = {'source_report': str(args.report), 'dataset_sha256': report['dataset_sha256'],
              'comparisons': [paired(report, variant, group=group) for variant in ('vector', 'vector_hybrid')
                              for group in ('all', 'monolingual', 'crosslingual')]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
