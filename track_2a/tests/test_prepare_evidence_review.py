import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('prepare_evidence_review', Path(__file__).parents[1]/'scripts/prepare_evidence_review.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_shared_reference_never_collapses_claim_specific_gold_annotations(tmp_path):
    dataset=tmp_path/'cases.jsonl'
    common=dict(reference_language='fr',reference_string='reference',booklet_file='source.pdf')
    dataset.write_text(''.join(json.dumps(dict(common,id=cid,claim=claim,entailment_label=label))+'\n'
                               for cid,claim,label in [('a','claim A',0),('b','claim B',2)]))
    key=hashlib.sha256(b'fr\nreference').hexdigest()
    report=tmp_path/'report.json'
    report.write_text(json.dumps(dict(dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        rows=[dict(variant='vector',reference_cluster=key,pre_budget_hits=[dict(page_number=7)])])))
    output=tmp_path/'packet.jsonl'
    assert module.prepare(dataset,report,output)==1
    group=json.loads(output.read_text())
    assert group['candidate_physical_pages']==[7]
    assert len(group['cases'])==2
    assert [r['entailment_label'] for r in group['cases']]==[0,2]
    assert all(r['gold_evidence_pages'] is None and r['review_status']=='unreviewed' for r in group['cases'])


def test_mismatched_source_is_rejected(tmp_path):
    dataset=tmp_path/'cases.jsonl'
    dataset.write_text('{}\n')
    report=tmp_path/'report.json'
    report.write_text(json.dumps({'dataset_sha256':'wrong'}))
    with pytest.raises(ValueError,match='does not match'):
        module.prepare(dataset,report,tmp_path/'packet.jsonl')
