"""Validate selected AI-reviewed source witnesses and score frozen contexts. Never gold recall."""
import argparse
import hashlib
import json
import re
from collections import defaultdict
from importlib.metadata import version
from pathlib import Path

from src.official_evaluation import normalize
from src.pdf_parser import PDFParser


def witness_normalize(text):
    # Preserve the official matcher; only this selected-witness scorer joins marked PDF soft line wraps.
    return normalize(re.sub(r'\u00ad[ \t]*\r?\n', '', text))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate(dataset, annotations, booklets):
    import pymupdf
    dataset_hash = sha(dataset.read_bytes())
    source = {r['id']: r for r in map(json.loads, dataset.read_text().splitlines())}
    rows = [json.loads(line) for line in annotations.read_text().splitlines() if line.strip()]
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Require nonempty annotations with unique case ids')
    parser = PDFParser()
    cache = {}
    for r in rows:
        original = source[r['id']]
        if r['annotation_tier'] != 'ai_reviewed_dev_silver' or r['human_reviewed'] is not False or r['independent_test'] is not False:
            raise ValueError('Only explicitly non-human, non-test AI silver annotations allowed')
        if r['review_status'] != 'codex_claude_checked':
            raise ValueError('Independent source review is not complete')
        if r['dataset_sha256'] != dataset_hash or r['official_label'] != original['entailment_label']:
            raise ValueError('Dataset or official label mismatch')
        if r['official_label'] not in (0, 2) or not r['selected_witnesses']:
            raise ValueError('Selected witnesses require non-neutral source labels')
        if r['claim_sha256'] != sha(original['claim'].encode()) or r['booklet_file'] != original['booklet_file']:
            raise ValueError('Source claim/booklet mismatch')
        pdf = booklets/r['booklet_file']
        if pdf.name not in cache:
            with pymupdf.open(pdf) as document:
                raw_pages = {i+1: p.get_text() for i,p in enumerate(document)}
            parsed = {p['page_number']: p['text'] for p in parser.extract_pages(pdf)}
            cache[pdf.name] = (sha(pdf.read_bytes()), raw_pages, parsed)
        digest, raw_pages, parsed = cache[pdf.name]
        if digest != r['booklet_sha256']:
            raise ValueError('Booklet hash mismatch')
        for witness in r['selected_witnesses']:
            page = witness['physical_page']
            quote = witness['quote']
            if type(page) is not int or page not in raw_pages or page not in parsed:
                raise ValueError('Invalid physical page')
            if not 40 <= len(witness_normalize(quote)) <= 5000:
                raise ValueError('Witness length must be specific and bounded')
            start, end = witness['source_char_start'], witness['source_char_end']
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw_pages[page]):
                raise ValueError('Invalid witness offsets')
            if raw_pages[page][start:end] != quote:
                raise ValueError('Quote/offset mismatch in source extraction')
            if witness_normalize(quote) not in witness_normalize(parsed[page]):
                raise ValueError('Quote not confirmed by project PDF extraction')
    return rows, source


def score(dataset, annotations, booklets, report_path, output):
    rows, source = validate(dataset, annotations, booklets)
    retrieval = json.loads(report_path.read_text())
    if retrieval['dataset_sha256'] != sha(dataset.read_bytes()):
        raise ValueError('Retrieval report does not match dataset')
    indexed = {}
    for r in retrieval['rows']:
        key = (r['id'], r['variant'])
        if key in indexed:
            raise ValueError('Duplicate case/variant in retrieval report')
        indexed[key] = r
    variants = sorted({r['variant'] for r in retrieval['rows']})
    results = []
    for ann in rows:
        original = source[ann['id']]
        for variant in variants:
            r = indexed[(ann['id'],variant)]
            if 'context_hits' not in r or 'pre_budget_hits' not in r:
                raise ValueError('Frozen full hit texts required; rerun with --save-hits')
            for hit in r['context_hits'] + r['pre_budget_hits']:
                if sha(hit['text'].encode()) != hit['text_sha256']:
                    raise ValueError('Frozen hit text hash mismatch')
            row = {'id': ann['id'], 'variant': variant, 'label': ann['official_label'],
                   'pair': original['claim_language']+'->'+original['reference_language'],
                   'crosslingual': original['claim_language'] != original['reference_language'],
                   'review_caveat':ann.get('review_caveat')}
            for stage, hits in [('pre_budget',r['pre_budget_hits']),('context',r['context_hits'])]:
                pages = {h['page_number'] for h in hits}
                row[stage+'_selected_page_hit'] = any(w['physical_page'] in pages for w in ann['selected_witnesses'])
                row[stage+'_complete_witness_hit'] = any(
                    h['page_number']==w['physical_page'] and witness_normalize(w['quote']) in witness_normalize(h['text'])
                    for w in ann['selected_witnesses'] for h in hits)
            results.append(row)
    groups = defaultdict(list)
    for row in results:
        for group in ('all','crosslingual' if row['crosslingual'] else 'monolingual', row['pair']):
            groups[(row['variant'],group)].append(row)
        if not row['review_caveat']:
            groups[(row['variant'],'without_review_caveats')].append(row)
    metrics = ('pre_budget_selected_page_hit','context_selected_page_hit',
               'pre_budget_complete_witness_hit','context_complete_witness_hit')
    summary = [{'variant':v,'group':g,'count':len(rs),**{k:sum(r[k] for r in rs)/len(rs) for k in metrics}}
               for (v,g),rs in sorted(groups.items())]
    result = {'kind':'selected_source_witness_dev_diagnostic','annotation_tier':'ai_reviewed_dev_silver',
        'human_reviewed':False,'independent_test':False,'annotation_count':len(rows),
        'dataset_sha256':sha(dataset.read_bytes()),'annotations_sha256':sha(annotations.read_bytes()),
        'retrieval_report_sha256':sha(report_path.read_bytes()),'scorer_sha256':sha(Path(__file__).read_bytes()),
        'extractors':{'pymupdf':version('PyMuPDF'),'project':'PDFParser.extract_pages'},
        'limitations':['Selected non-exhaustive witnesses; failure may mean an unannotated alternative was retrieved.',
            'Page hit alone does not imply the witness text survived chunking/clipping.',
            'Complete witness requires one passage to contain the entire normalized quote; valid distributed evidence may fail.',
            'AI-reviewed observed dev, no human gold, no sealed test, no recall or significance claim.',
            'Annotation selection covers all six source Contradictions plus six Entailments spanning nine pairs; no Neutral absence labels.'],
        'summary':summary,'rows':results}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--annotations',type=Path,required=True)
    parser.add_argument('--booklets',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=score(args.dataset,args.annotations,args.booklets,args.report,args.output)
    print(f"Validated {result['annotation_count']} silver annotations; wrote {len(result['rows'])} rows")


if __name__=='__main__':
    main()
