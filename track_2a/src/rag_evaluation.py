"""Paired retrieval dev evaluation; gold is used only after ranking."""
import argparse
import hashlib
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from src import config
from src.apertus_client import ApertusClient
from src.evaluator import load_records, _macro_f1
from src.inference import ClaimVerificationEngine
from src.official_evaluation import evidence_hit5
from src.part_context import claim_part
from src.retriever import PassageRetriever
from src.vector_rag import E5Encoder, VectorRetriever


def budget_hits(hits, tokenizer, budget):
    selected, used = [], 0
    for hit in hits:
        offsets = tokenizer(hit['text'], add_special_tokens=False, return_offsets_mapping=True)['offset_mapping']
        remaining = budget - used
        if remaining <= 0:
            break
        if len(offsets) > remaining:
            selected.append({**hit, 'text': hit['text'][:offsets[remaining - 1][1]]})
            break
        selected.append(hit)
        used += len(offsets)
    return selected


def reference_metrics(hits, reference, tokenizer, k):
    gold = set(tokenizer.encode(reference, add_special_tokens=False))
    found = set(tokenizer.encode(' '.join(p['text'] for p in hits[:k]), add_special_tokens=False))
    matches = [evidence_hit5({'evidence': [{'text': p['text']}]}, reference) for p in hits[:k]]
    return {'reference_hit_at_k': any(matches), 'reference_hit_at_5': any(matches[:5]),
            'reference_token_coverage': len(gold & found) / len(gold) if gold else None}


def mean_available(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return sum(values)/len(values) if values else None


def run(args):
    raw_records = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    records = load_records(args.dataset)
    for rec, raw in zip(records, raw_records):
        rec['gold_evidence_pages'] = raw.get('gold_evidence_pages')
        if rec['gold_evidence_pages'] is not None and (not rec['gold_evidence_pages'] or
                any(type(p) is not int or p < 1 for p in rec['gold_evidence_pages'])):
            raise ValueError('gold_evidence_pages must contain positive physical PDF page integers')
    if args.limit:
        records = records[:args.limit]
    if not records or len({r['id'] for r in records}) != len(records):
        raise ValueError('Require nonempty records with unique ids')
    if any(not r['reference_string'] for r in records):
        raise ValueError('Reference text required for retrieval proxy metrics')
    client = ApertusClient()
    variants = ('baseline_native', 'baseline_budgeted', 'bm25_pages', 'bm25_chunks', 'vector', 'vector_hybrid')
    if args.nli and (client.mock or not client.api_key):
        raise ValueError('Real NLI requested but API key missing or mock active; refusing quality scores')
    if args.nli and len(records) * len(variants) > args.max_nli_calls:
        raise ValueError('NLI call cap exceeded; lower --limit or explicitly raise --max-nli-calls')
    previous_query_mode, previous_translate = config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM
    config.RETRIEVAL_QUERY_MODE = 'original'
    config.TRANSLATE_CLAIM = False
    engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True), prompt_mode='ids')
    # Capture production retrieval before any NLI. No monkey patch of shared class/state.
    engine._compact_predict = lambda **kwargs: SimpleNamespace(passages=kwargs['passages'], tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)
    started = time.perf_counter()
    try:
        encoder = E5Encoder(revision=args.revision)
    except BaseException:
        engine.close()
        config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM = previous_query_mode, previous_translate
        raise
    tokenizer = encoder.model.tokenizer
    report = {'kind': 'paired_dev_regression', 'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'nli': {'enabled': args.nli, 'client_mock': client.mock, 'model': client.model_name,
                      'prompt': 'ApertusClient.infer JSON identical across variants; not production NLI pipeline'},
              'embedding': encoder.identity, 'model_load_ms': (time.perf_counter()-started)*1000,
              'baseline_config': {k: getattr(config, k) for k in ('PART_CONTEXT', 'PART_CONTEXT_CHARS', 'PART_CONTEXT_CHARS_LEGAL', 'PAGE_MAX_CHARS', 'SPEAKER_AWARE', 'SPEAKER_BOOST', 'DEFAULT_TOP_K')},
              'top_k': args.top_k, 'context_budget_e5_tokens': args.budget, 'setup': [], 'rows': []}
    try:
        for pdf in sorted({r['booklet_pdf'] for r in records}):
            start = time.perf_counter()
            data = engine._get_booklet_data(pdf)
            parse_ms = (time.perf_counter()-start)*1000
            vector = None
            try:
                start = time.perf_counter()
                vector = VectorRetriever(data['paragraphs'], encoder=encoder, index_dir=args.index_dir)
                index_ms = (time.perf_counter()-start)*1000
                pages = PassageRetriever(data['paragraphs'])
                vector.retrieve('Abstimmung', top_k=1)  # warmup without labels
                report['setup'].append({'booklet': pdf.name, 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    'parse_or_cache_ms': parse_ms, 'index_build_or_open_ms': index_ms, 'index': vector.metadata})
                for rec in (r for r in records if r['booklet_pdf'] == pdf):
                    for variant in variants:
                        start = time.perf_counter()
                        if variant.startswith('baseline'):
                            hits = engine.verify_claim(rec['claim'], pdf, vote=rec['vote'], strategy='hybrid',
                                top_k=config.DEFAULT_TOP_K if variant == 'baseline_native' else args.top_k,
                                claim_language=rec['claim_language'], booklet_language=rec['reference_language']).passages
                        elif variant.startswith('bm25'):
                            retriever = pages if variant == 'bm25_pages' else vector.bm25
                            hits = retriever.retrieve_hybrid(rec['claim'], top_k=args.top_k, target_vote=rec['vote'])
                        else:
                            hits = vector.retrieve(rec['claim'], top_k=args.top_k, target_vote=rec['vote'], mode=variant)
                        retrieval_ms = (time.perf_counter()-start)*1000
                        if variant != 'baseline_native':
                            hits = budget_hits(hits if variant == 'baseline_budgeted' else hits[:args.top_k], tokenizer, args.budget)
                        row = {'id': rec['id'], 'variant': variant, 'group': 'cue' if claim_part(rec['claim']) else 'free',
                            'pair': f"{rec['claim_language']}->{rec['reference_language']}", 'retrieval_ms': retrieval_ms,
                            'context_e5_tokens': sum(len(tokenizer.encode(p['text'], add_special_tokens=False)) for p in hits),
                            'pages': [p['page_number'] for p in hits],
                            'distinct_pages': len({p['page_number'] for p in hits}),
                            'timing_scope': 'retrieval plus pipeline preprocessing' if variant.startswith('baseline') else 'retriever only',
                            **reference_metrics(hits, rec['reference_string'], tokenizer, args.top_k)}
                        gold_pages = set(rec['gold_evidence_pages'] or [])
                        retrieved_pages = set(row['pages'][:args.top_k])
                        row['retrieval_hit_at_k'] = bool(gold_pages & retrieved_pages) if gold_pages else None
                        row['retrieval_recall_at_k'] = len(gold_pages & retrieved_pages)/len(gold_pages) if gold_pages else None
                        if args.nli:
                            out = client.infer(context='\n\n'.join(f"[Page {p['page_number']}] {p['text']}" for p in hits),
                                claim=rec['claim'], claim_language=rec['claim_language'])
                            if out.error or client.model_name != report['nli']['model']:
                                raise RuntimeError('NLI error/model switch: incomplete comparison discarded')
                            row.update(gold=rec['label'], predicted=out.label, prompt_tokens=out.tokens_prompt,
                                completion_tokens=out.tokens_completion, nli_ms=out.latency_ms,
                                evidence_hit_at_5=evidence_hit5({'evidence': [{'text': e} for e in out.evidence]}, rec['reference_string']))
                        report['rows'].append(row)
            finally:
                if vector is not None:
                    vector.close()
    finally:
        engine.close()
        config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM = previous_query_mode, previous_translate
    groups = defaultdict(list)
    for row in report['rows']:
        for group in ('all', row['group'], row['pair']):
            groups[(row['variant'], group)].append(row)
    report['summary'] = []
    for (variant, group), rows in sorted(groups.items()):
        keys = ['reference_hit_at_k', 'reference_hit_at_5', 'reference_token_coverage', 'retrieval_ms', 'context_e5_tokens']
        if args.nli:
            keys += ['prompt_tokens', 'completion_tokens', 'nli_ms', 'evidence_hit_at_5']
        report['summary'].append({'variant': variant, 'group': group, 'count': len(rows),
            **{k: sum(r[k] for r in rows)/len(rows) for k in keys},
            'retrieval_hit_at_k': mean_available(rows, 'retrieval_hit_at_k'),
            'retrieval_recall_at_k': mean_available(rows, 'retrieval_recall_at_k'),
            'small_sample': len(rows) < 30,
            'gold_page_count': sum(r['retrieval_hit_at_k'] is not None for r in rows),
            'nli_macro_f1': _macro_f1([r['gold'] for r in rows], [r['predicted'] for r in rows]) if args.nli else None,
            'evidence_hit_at_5': sum(r['evidence_hit_at_5'] for r in rows)/len(rows) if args.nli else None})
    report['limitations'] = ['Existing data are observed dev/regression, never held-out test.',
        'Reference fuzzy hits and unique token coverage are proxies, not gold evidence Recall@k: no span annotations.',
        'bm25_chunks/vector/vector_hybrid isolate ranking on identical chunks and budget without speaker rules.',
        'baseline_native retains production context; baseline_budgeted applies an E5 text budget in production reading order, without top-k slicing of cue parts.',
        'Baseline timings include numerical checking/preprocessing and are not directly comparable to retriever-only times.',
        'Reference hit proxy depends on unit size and official 5000-character quote limit; compare ranking primarily on same chunks.',
        'Native baseline has its production budget and is not a controlled embedding ablation.',
        'NLI uses a constant JSON prompt rather than production NLI; small sample groups are anecdotal.',
        'max-nli-calls caps logical infer calls, not internal retries or repair requests.',
        'E5 context tokens exclude prompt formatting; actual NLI tokens only available with --nli.',
        'Warm query times include query encoding; setup reports index build OR reopen, not cold-only build.']
    report['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), Path(__file__).with_name('vector_rag.py'),
                  Path(__file__).with_name('inference.py'), Path(__file__).with_name('apertus_client.py')]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f"Wrote {len(report['rows'])} paired rows to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=config.BASE_DIR/'data/demo_dataset.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index-dir', type=Path, default=config.BASE_DIR/'.rag-index')
    parser.add_argument('--revision', default='614241f622f53c4eeff9890bdc4f31cfecc418b3')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--budget', type=int, default=1536)
    parser.add_argument('--nli', action='store_true')
    parser.add_argument('--max-nli-calls', type=int, default=15)
    args = parser.parse_args()
    if args.top_k < 1 or args.budget < 1 or (args.limit is not None and args.limit < 1):
        parser.error('top-k, budget and limit must be positive')
    run(args)


if __name__ == '__main__':
    main()
