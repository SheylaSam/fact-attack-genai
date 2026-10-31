"""Paired retrieval dev evaluation; gold is used only after ranking."""
import argparse
import hashlib
import json
import re
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


def lexical_overlap(hits, reference, n=5):
    """Exact word n-gram overlap with the reference, not semantic evidence recall."""
    def grams(text):
        words = re.findall(r"\w+", text.casefold())
        return {tuple(words[i:i+n]) for i in range(len(words)-n+1)}
    gold = grams(reference)
    found = set().union(*(grams(hit['text']) for hit in hits)) if hits else set()
    shared = gold & found
    return {'reference_5gram_recall': len(shared)/len(gold) if gold else None,
            'reference_5gram_precision': len(shared)/len(found) if found else None}


def validate_gold_pages(value):
    if value is not None and (not isinstance(value, list) or
            any(type(page) is not int or page < 1 for page in value)):
        raise ValueError('gold_evidence_pages must be null or a list of positive physical PDF pages')
    return value


def hit_trace(hits):
    return [{'position': i, 'page_number': p['page_number'],
             'parent_index': p.get('parent_index'), 'chunk_number': p.get('chunk_number'),
             'score': float(p['retrieval_score']) if p.get('retrieval_score') is not None else None,
             'text_sha256': hashlib.sha256(p['text'].encode()).hexdigest(), 'text': p['text']}
            for i, p in enumerate(hits, 1)]


def mean_available(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    return sum(values)/len(values) if values else None


def evidence_score(rows):
    eligible = [r for r in rows if r['gold'] != 1]
    return sum(r['evidence_hit_at_5'] for r in eligible)/len(eligible) if eligible else None


def run(args):
    raw_records = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    records = load_records(args.dataset)
    for rec, raw in zip(records, raw_records):
        rec['gold_evidence_pages'] = validate_gold_pages(raw.get('gold_evidence_pages'))
    if getattr(args, 'case_ids', None):
        requested = set(args.case_ids)
        if len(requested) != len(args.case_ids) or not requested.issubset({r['id'] for r in records}):
            raise ValueError('Requested case ids must be unique and exist in dataset')
        records = [r for r in records if r['id'] in requested]
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
    engine = None
    try:
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True), prompt_mode='ids')
        # Capture production retrieval before any NLI. No monkey patch of shared class/state.
        engine._compact_predict = lambda **kwargs: SimpleNamespace(passages=kwargs['passages'], tokens_prompt=0, tokens_completion=0, tokens_total=0, latency_ms=0)
        started = time.perf_counter()
        encoder = E5Encoder(revision=args.revision, chunk_tokens=getattr(args, 'chunk_tokens', 384),
                            overlap=getattr(args, 'overlap', 64))
        tokenizer = encoder.model.tokenizer
        report = {'schema_version': 2, 'kind': 'paired_dev_regression', 'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                  'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  'nli': {'enabled': args.nli, 'client_mock': client.mock, 'model': client.model_name,
                          'prompt': 'ApertusClient.infer JSON identical across variants; not production NLI pipeline'},
                  'embedding': encoder.identity, 'model_load_ms': (time.perf_counter()-started)*1000,
                  'baseline_config': {k: getattr(config, k) for k in ('PART_CONTEXT', 'PART_CONTEXT_CHARS', 'PART_CONTEXT_CHARS_LEGAL', 'PAGE_MAX_CHARS', 'SPEAKER_AWARE', 'SPEAKER_BOOST', 'DEFAULT_TOP_K')},
                  'retrieval_policy': {'query_translation': 'disabled', 'claim_translation': 'disabled',
                      'baseline_budget': 'native or shared E5 text budget; context order retained'},
                  'selected_case_ids': [r['id'] for r in records],
                  'top_k': args.top_k, 'context_budget_e5_tokens': args.budget, 'setup': [], 'rows': []}
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
                    if rec['gold_evidence_pages'] and not set(rec['gold_evidence_pages']).issubset(
                            {p['page_number'] for p in data['pages']}):
                        raise ValueError('Gold page is outside the parsed physical PDF pages')
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
                        raw_hits = hits
                        pre_budget = {**reference_metrics(raw_hits, rec['reference_string'], tokenizer, args.top_k),
                                      **lexical_overlap(raw_hits, rec['reference_string'])}
                        if variant != 'baseline_native':
                            hits = budget_hits(hits if variant == 'baseline_budgeted' else hits[:args.top_k], tokenizer, args.budget)
                        row = {'id': rec['id'], 'variant': variant, 'group': 'cue' if claim_part(rec['claim']) else 'free',
                            'reference_cluster': hashlib.sha256((rec['reference_language']+'\n'+rec['reference_string']).encode()).hexdigest(),
                            'crosslingual': rec['claim_language'] != rec['reference_language'],
                            'context_order': 'production_context_order' if variant.startswith('baseline') else 'retrieval_rank',
                            'pre_budget': pre_budget,
                            'pair': f"{rec['claim_language']}->{rec['reference_language']}", 'retrieval_ms': retrieval_ms,
                            'context_e5_tokens': sum(len(tokenizer.encode(p['text'], add_special_tokens=False)) for p in hits),
                            'pages': [p['page_number'] for p in hits],
                            'distinct_pages': len({p['page_number'] for p in hits}),
                            'timing_scope': 'retrieval plus pipeline preprocessing' if variant.startswith('baseline') else 'retriever only',
                            **reference_metrics(hits, rec['reference_string'], tokenizer, args.top_k),
                            **lexical_overlap(hits, rec['reference_string']),
                            'context_reference_hit': any(evidence_hit5({'evidence': [{'text': p['text']}]}, rec['reference_string']) for p in hits)}
                        row['budget_lost_reference_hit'] = pre_budget['reference_hit_at_k'] and not row['reference_hit_at_k']
                        if getattr(args, 'save_hits', False):
                            row['pre_budget_hits'] = hit_trace(raw_hits)
                            row['context_hits'] = hit_trace(hits)
                        row['gold_page_annotation_status'] = ('unknown' if rec['gold_evidence_pages'] is None else
                            'reviewed_empty' if not rec['gold_evidence_pages'] else 'pages_supplied')
                        gold_pages = set(rec['gold_evidence_pages'] or [])
                        retrieved_pages = set(row['pages'][:args.top_k])
                        row['retrieval_hit_at_k'] = bool(gold_pages & retrieved_pages) if gold_pages else None
                        row['retrieval_recall_at_k'] = len(gold_pages & retrieved_pages)/len(gold_pages) if gold_pages else None
                        raw_pages = {p['page_number'] for p in raw_hits[:args.top_k]}
                        pre_budget['retrieval_hit_at_k'] = bool(gold_pages & raw_pages) if gold_pages else None
                        pre_budget['retrieval_recall_at_k'] = len(gold_pages & raw_pages)/len(gold_pages) if gold_pages else None
                        row['context_page_hit'] = bool(gold_pages & set(row['pages'])) if gold_pages else None
                        row['context_page_recall'] = len(gold_pages & set(row['pages']))/len(gold_pages) if gold_pages else None
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
        try:
            if engine is not None:
                engine.close()
        finally:
            config.RETRIEVAL_QUERY_MODE, config.TRANSLATE_CLAIM = previous_query_mode, previous_translate
    groups = defaultdict(list)
    for row in report['rows']:
        for group in ('all', row['group'], row['pair'], 'crosslingual' if row['crosslingual'] else 'monolingual'):
            groups[(row['variant'], group)].append(row)
    report['summary'] = []
    for (variant, group), rows in sorted(groups.items()):
        keys = ['reference_hit_at_k', 'reference_hit_at_5', 'reference_token_coverage', 'retrieval_ms', 'context_e5_tokens']
        if args.nli:
            keys += ['prompt_tokens', 'completion_tokens', 'nli_ms']
        clusters = defaultdict(list)
        for row in rows:
            clusters[row['reference_cluster']].append(row)
        report['summary'].append({'variant': variant, 'group': group, 'count': len(rows),
            **{k: sum(r[k] for r in rows)/len(rows) for k in keys},
            'reference_cluster_count': len(clusters),
            'cluster_mean_reference_hit_at_k': sum(sum(r['reference_hit_at_k'] for r in cluster)/len(cluster) for cluster in clusters.values())/len(clusters),
            'reference_5gram_recall': mean_available(rows, 'reference_5gram_recall'),
            'reference_5gram_precision': mean_available(rows, 'reference_5gram_precision'),
            'pre_budget_reference_hit_at_k': sum(r['pre_budget']['reference_hit_at_k'] for r in rows)/len(rows),
            'budget_lost_reference_hit_count': sum(r['budget_lost_reference_hit'] for r in rows),
            'context_reference_hit': sum(r['context_reference_hit'] for r in rows)/len(rows),
            'retrieval_hit_at_k': mean_available(rows, 'retrieval_hit_at_k'),
            'retrieval_recall_at_k': mean_available(rows, 'retrieval_recall_at_k'),
            'context_page_hit': mean_available(rows, 'context_page_hit'),
            'context_page_recall': mean_available(rows, 'context_page_recall'),
            'reviewed_empty_page_count': sum(r['gold_page_annotation_status']=='reviewed_empty' for r in rows),
            'small_sample': len(rows) < 30 or len(clusters) < 30,
            'gold_page_count': sum(r['retrieval_hit_at_k'] is not None for r in rows),
            'nli_macro_f1': _macro_f1([r['gold'] for r in rows], [r['predicted'] for r in rows]) if args.nli else None,
            'evidence_hit_at_5': evidence_score(rows) if args.nli else None,
            'evidence_eligible_count': sum(r['gold'] != 1 for r in rows) if args.nli else 0})
    report['limitations'] = ['Baseline first-k proxy metrics use context order, not retrieval rank; context_reference_hit considers every supplied passage.',
        'Short clipped passages may spuriously match the legacy fuzzy proxy. Exact 5-gram overlap is a separate lexical diagnostic, not evidence gold.',
        'Reference clusters share reference text, not necessarily underlying claim/paraphrase families; no independence claim.',
        'Existing data are observed dev/regression, never held-out test.',
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
                  Path(__file__).with_name('inference.py'), Path(__file__).with_name('apertus_client.py'),
                  Path(__file__).with_name('pdf_parser.py'), Path(__file__).with_name('text_utils.py')]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f"Wrote {len(report['rows'])} paired rows to {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=config.BASE_DIR/'data/demo_dataset.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index-dir', type=Path, default=config.BASE_DIR/'.rag-index')
    parser.add_argument('--revision', default='614241f622f53c4eeff9890bdc4f31cfecc418b3')
    parser.add_argument('--case-ids', nargs='+', help='Explicit paired subset; all ids must exist')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--budget', type=int, default=1536)
    parser.add_argument('--chunk-tokens', type=int, default=384)
    parser.add_argument('--overlap', type=int, default=64)
    parser.add_argument('--save-hits', action='store_true', help='Save full pre-budget and context texts for error analysis')
    parser.add_argument('--nli', action='store_true')
    parser.add_argument('--max-nli-calls', type=int, default=15)
    args = parser.parse_args()
    if args.top_k < 1 or args.budget < 1 or (args.limit is not None and args.limit < 1):
        parser.error('top-k, budget and limit must be positive')
    if not 0 <= args.overlap < args.chunk_tokens:
        parser.error('Require 0 <= overlap < chunk-tokens')
    run(args)


if __name__ == '__main__':
    main()
