import importlib.util
import json
from pathlib import Path
import sys
import pytest

spec=importlib.util.spec_from_file_location('run_nli42',Path(__file__).parents[1]/'scripts/run_nli42.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def row(cid,variant,error=None):
    return dict(id=cid,variant=variant,true_label=1,predicted=None if error else 1,error=error,
        pair='de->fr',crosslingual=True,claim_group='cue',evidence_hit_at_5=False,
        prompt_tokens=100,completion_tokens=20,latency_ms=50,context_e5_tokens=80,retrieval_ms=1)


def test_one_failed_variant_excludes_the_whole_case_for_every_arm():
    rows=[row(cid,variant,'parse' if cid=='bad' and variant=='vector' else None)
          for cid in ('good','bad') for variant in module.VARIANTS]
    report=module.summarize(rows,{'good':{},'bad':{}})
    assert report['paired_case_count']==1 and report['excluded_case_ids']==['bad']
    assert all(s['count']==1 for s in report['summary'])
    assert {s['variant'] for s in report['summary']}==set(module.VARIANTS)
    assert all(s['macro_f1']==pytest.approx(1/3,abs=.0001) for s in report['summary'])


def test_duplicate_scored_pair_is_an_error():
    with pytest.raises(ValueError,match='Duplicate'):
        module.complete_case_ids([row('a','vector'),row('a','vector')])


def test_real_reuse_requires_matching_nli_source_and_model(tmp_path):
    path=tmp_path/'prior.json'
    path.write_text(json.dumps(dict(complete=True,mock=False,model='model',dataset_sha256='dataset',
        nli_source_sha256='old',prompt=module.PROMPT,served_models=['model'],rows=[])))
    with pytest.raises(ValueError,match='not compatible'):
        module.audited_cache(path,'model','dataset','new')


def test_full_cohort_preflight_rejects_missing_pair_before_any_access(tmp_path):
    source=[dict(id=f'case-{i}',entailment_label=i%3,claim_language='de',reference_language='fr') for i in range(42)]
    dataset=tmp_path/'cases.jsonl';dataset.write_text(''.join(json.dumps(r)+'\n' for r in source))
    rows=[dict(id=r['id'],variant=v,pair='de->fr',context_hits=[dict(page_number=1,text='source',text_sha256=module.sha(b'source'))])
          for r in source for v in module.VARIANTS]
    report=tmp_path/'frozen.json';report.write_text(json.dumps(dict(dataset_sha256=module.sha(dataset.read_bytes()),rows=rows[:-1])))
    with pytest.raises(ValueError,match='complete identical pairing'):
        module.prepare_jobs(dataset,report)


def test_attempt_126_is_the_last_possible_send(tmp_path):
    with (tmp_path/'journal').open('w') as stream:
        budget=module.AttemptBudget(126,stream,max_allowed=126)
        for _ in range(126):budget.reserve('POST',b'payload')
        with pytest.raises(RuntimeError,match='budget'):budget.reserve('POST',b'extra')
    assert budget.used==126


def test_response_schema_requires_real_probabilities_and_evidence_list():
    base=dict(label=1,p_entail=0.0,p_neutral=1.0,p_contra=0.0,reasoning='No evidence',evidence=[])
    assert module.validate_response(json.dumps(base))==1
    for key,value in [('p_entail',True),('p_neutral',2),('p_contra',None),('evidence','quote'),('reasoning',7)]:
        with pytest.raises(ValueError):
            module.validate_response(json.dumps({**base,key:value}))
