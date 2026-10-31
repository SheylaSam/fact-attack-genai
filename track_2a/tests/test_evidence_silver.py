import json
from pathlib import Path
from unittest.mock import patch
import pytest
from src.evidence_silver import witness_normalize, validate, score
from src.pdf_parser import PDFParser

ROOT=Path(__file__).parents[1]
DATA=ROOT/'data/genai/ost_june_dev.jsonl'
ANN=ROOT/'data/genai/evidence_silver12.jsonl'


def test_normalization_only_joins_marked_line_wraps():
    assert witness_normalize('Parla\u00ad\nment')=='parlament'
    assert witness_normalize('Parla-\nment')=='parlament'
    assert witness_normalize('Parla ment')!='parlament'
    assert witness_normalize('EU-Vertrag')=='eu-vertrag'


def test_whole_page_dehyphenation_preserves_real_hyphens_and_source_text():
    pages=[dict(page_number=4,text='Erste Vorlage: Test\nDas Parla-\nment beurteilt den EU-Vertrag und die Zu-\nwanderung.')]
    original=pages[0]['text']
    result=PDFParser().extract_paragraphs('unused.pdf',passage_chars=None,pages=pages)
    text=' '.join(r['text'] for r in result)
    assert 'Parlament' in text and 'Zuwanderung' in text and 'EU-Vertrag' in text
    assert pages[0]['text']==original


def test_silver_validator_rejects_label_changes_and_unreviewed_annotation(tmp_path):
    original=json.loads(ANN.read_text().splitlines()[0])
    for field,value,error in [('official_label',2,'label mismatch'),
                              ('review_status','unreviewed','not complete'),
                              ('human_reviewed',True,'non-human')]:
        row={**original,field:value}
        path=tmp_path/'bad.jsonl';path.write_text(json.dumps(row)+'\n')
        with pytest.raises(ValueError,match=error):
            validate(DATA,path,ROOT/'data/booklets')


def test_tampered_quote_cannot_pass_exact_source_validation(tmp_path):
    row=json.loads(ANN.read_text().splitlines()[0])
    row['selected_witnesses'][0]['quote']='Invented quotation that does not occur in the actual source booklet.'
    path=tmp_path/'bad.jsonl';path.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='Quote/offset mismatch'):
        validate(DATA,path,ROOT/'data/booklets')


def test_frozen_context_integrity_checked_before_scoring(tmp_path):
    r=json.loads((ROOT/'results/genai/ost_june_chunk384.json').read_text())
    r['rows'][0]['context_hits'][0]['text']='tampered'
    path=tmp_path/'bad.json';path.write_text(json.dumps(r))
    with patch('src.evidence_silver.validate',return_value=([{'id':r['rows'][0]['id'],'official_label':0,
            'selected_witnesses':[{'physical_page':1,'quote':'source'}]}],
            {r['rows'][0]['id']:{'claim_language':'de','reference_language':'fr'}})):
        with pytest.raises(ValueError,match='hash mismatch'):
            score(DATA,ANN,ROOT/'data/booklets',path,tmp_path/'out.json')
