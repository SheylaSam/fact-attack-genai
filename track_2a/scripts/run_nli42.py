"""Paired real NLI evaluation of the fixed 42-case observed dev cohort.

At most 126 actual POST attempts, errors are not labels, audited exact-input
responses may be reused. No automatic paid resume or overwritten journals.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import re
import os
from pathlib import Path
import sys
import statistics
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))
from fixed_context_nli_smoke import AttemptBudget, CountedConnection, sha, strict_label
from dotenv import dotenv_values
from src import config
from src.apertus_client import ApertusClient
from src.evaluator import _macro_f1
from src.official_evaluation import evidence_hit5
from src.part_context import claim_part

VARIANTS=('baseline_native','vector','vector_hybrid')
PROMPT='Constant ApertusClient.infer JSON, raw Page joins, not production NLI pipeline'


def validate_response(content):
    label=strict_label(content)
    text=content.rsplit('<|inner_suffix|>',1)[-1].strip()
    if text.startswith('```') and text.endswith('```'):
        text=re.sub(r'^```(?:json)?\s*','',text)[:-3].strip()
    parsed=json.loads(text)
    for field in ('p_entail','p_neutral','p_contra'):
        value=parsed.get(field)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
            raise ValueError('Missing or invalid probability')
    if not isinstance(parsed.get('reasoning'),str) or not isinstance(parsed.get('evidence'),list) or any(not isinstance(e,str) for e in parsed['evidence']):
        raise ValueError('Malformed reasoning or evidence schema')
    return label


def prepare_jobs(dataset, report):
    records=[json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    source={r['id']:r for r in records}
    if len(records)!=42 or len(source)!=42 or {r['entailment_label'] for r in records}!={0,1,2}:
        raise ValueError('Require all 42 unique original dev cases and all three labels')
    frozen=json.loads(report.read_text())
    if frozen['dataset_sha256']!=sha(dataset.read_bytes()):
        raise ValueError('Frozen contexts do not match source dataset')
    if set(r['variant'] for r in frozen['rows'])!=set(VARIANTS):
        raise ValueError('Require exactly the three predeclared retrieval variants')
    indexed={}
    for row in frozen['rows']:
        key=(row['id'],row['variant'])
        if key in indexed:
            raise ValueError('Duplicate frozen case/variant')
        indexed[key]=row
    if set(indexed)!={(cid,v) for cid in source for v in VARIANTS}:
        raise ValueError('Require complete identical pairing of all 42 cases')
    jobs=[]
    for index,(cid,rec) in enumerate(source.items()):
        order=VARIANTS[index%3:]+VARIANTS[:index%3]
        for variant in order:
            frozen_row=indexed[(cid,variant)]
            hits=frozen_row['context_hits']
            if frozen_row['pair']!=rec['claim_language']+'->'+rec['reference_language']:
                raise ValueError('Claim/reference language identity mismatch')
            if any(sha(h['text'].encode())!=h['text_sha256'] for h in hits):
                raise ValueError('Frozen hit hash mismatch')
            context='\n\n'.join(f"[Page {h['page_number']}] {h['text']}" for h in hits)
            jobs.append((cid,variant,context,frozen_row))
    return source,jobs


def complete_case_ids(rows):
    by_case=defaultdict(dict)
    for row in rows:
        if row['variant'] in by_case[row['id']]:
            raise ValueError('Duplicate scored case/variant')
        by_case[row['id']][row['variant']]=row
    return {cid for cid,rs in by_case.items() if set(rs)==set(VARIANTS) and
            all(r.get('error') is None and r.get('predicted') in (0,1,2) for r in rs.values())}


def summarize(rows, source):
    complete=complete_case_ids(rows)
    valid=[r for r in rows if r['id'] in complete]
    groups=defaultdict(list)
    for r in valid:
        for group in ('all',r['pair'],'crosslingual' if r['crosslingual'] else 'monolingual',r['claim_group']):
            groups[(r['variant'],group)].append(r)
    summary=[]
    for (variant,group),rs in sorted(groups.items()):
        eligible=[r for r in rs if r['true_label']!=1]
        fresh=[r for r in rs if r.get('origin')=='new_remote']
        latencies=sorted(r['latency_ms'] for r in fresh)
        summary.append({'variant':variant,'group':group,'count':len(rs),
            'macro_f1':_macro_f1([r['true_label'] for r in rs],[r['predicted'] for r in rs]),
            'accuracy':sum(r['true_label']==r['predicted'] for r in rs)/len(rs),
            'evidence_eligible_count':len(eligible),
            'evidence_hit_at_5':sum(r['evidence_hit_at_5'] for r in eligible)/len(eligible) if eligible else None,
            **{f'mean_{key}':sum(r[key] for r in rs)/len(rs) for key in
               ('prompt_tokens','completion_tokens','latency_ms','context_e5_tokens','retrieval_ms')},
            'source_label_counts':dict(Counter(str(r['true_label']) for r in rs)),
            'fresh_response_count':len(fresh),
            'estimated_usage_count':sum(r.get('usage_estimated',True) for r in rs),
            'mean_fresh_latency_ms':statistics.mean(latencies) if latencies else None,
            'median_fresh_latency_ms':statistics.median(latencies) if latencies else None,
            'p90_fresh_latency_ms':latencies[min(len(latencies)-1,int(.9*(len(latencies)-1)))] if latencies else None,
            'arbiter_changed_label_count':sum(r.get('raw_label',r['predicted'])!=r['predicted'] for r in rs),
            'small_sample':len(rs)<30})
    flips=[]
    index={(r['id'],r['variant']):r for r in valid}
    for variant in VARIANTS[1:]:
        pairs=[(index[(cid,'baseline_native')],index[(cid,variant)]) for cid in sorted(complete)]
        flips.append({'variant':variant,'paired_count':len(pairs),
            'corrected_baseline_error_ids':[a['id'] for a,b in pairs if a['predicted']!=a['true_label'] and b['predicted']==b['true_label']],
            'introduced_error_ids':[a['id'] for a,b in pairs if a['predicted']==a['true_label'] and b['predicted']!=b['true_label']],
            'changed_label_ids':[a['id'] for a,b in pairs if a['predicted']!=b['predicted']]})
    intent_to_treat=[]
    for variant in VARIANTS:
        rs={r['id']:r for r in rows if r['variant']==variant}
        truth=[r['entailment_label'] for r in source.values() if 'entailment_label' in r]
        if len(truth)==len(source):
            predictions=[rs[cid]['predicted'] if cid in rs and rs[cid].get('error') is None else -1 for cid in source]
            intent_to_treat.append({'variant':variant,'expected_count':len(source),
                'unprocessed_count':len(source)-len(rs),'error_counts':dict(Counter(r['error'] for r in rs.values() if r['error'])),
                'macro_f1_errors_as_misses':_macro_f1(truth,predictions),
                'accuracy_errors_as_misses':sum(a==b for a,b in zip(truth,predictions))/len(source)})
    return {'intent_to_treat':intent_to_treat,'paired_case_count':len(complete),'excluded_case_ids':sorted(set(source)-complete),
            'summary':summary,'paired_changes':flips}


def audited_cache(path, model, dataset_hash, nli_hash):
    if path is None:
        return {}
    r=json.loads(path.read_text())
    if (not r.get('complete') or r.get('mock') is not False or r['model']!=model or
        r['dataset_sha256']!=dataset_hash or r['nli_source_sha256']!=nli_hash or r['prompt']!=PROMPT or
        r['served_models']!=[model]):
        raise ValueError('Prior real response cache is not compatible with this run')
    journal_path=path.with_suffix('.journal.jsonl')
    receipts={}
    pending=None
    for event in map(json.loads,journal_path.read_text().splitlines()):
        if event['kind']=='response_received':
            pending=event
        elif event['kind']=='result' and event['input_identity'] not in receipts and pending is not None:
            receipts[event['input_identity']]=pending
            pending=None
    cache={}
    for row in r['rows']:
        if row['predicted'] not in (0,1,2) or type(row['raw_label']) is not int:
            raise ValueError('Invalid cached inference')
        receipt=receipts.get(row['input_identity'])
        if (receipt is None or receipt['model']!=model or receipt['finish_reason']!='stop' or
                validate_response(receipt['content'])!=row['raw_label']):
            raise ValueError('Prior raw response cannot be independently validated')
        cache[row['input_identity']]={'usage_estimated':False,'predicted':row['predicted'],'raw_label':row['raw_label'],
            'raw_evidence':row['raw_evidence'],'decision_rule':row['decision_rule'],
            'prompt_tokens':row['prompt_tokens'],'completion_tokens':row['completion_tokens'],
            'latency_ms':row['latency_ms'],'origin':'audited_prior_smoke'}
    return cache


def run(args):
    source,jobs=prepare_jobs(args.dataset,args.report)
    if not 1<=args.max_requests<=126:
        raise ValueError('Hard HTTP limit must be 1 through 126')
    settings=dotenv_values(args.env_file)
    key=settings.get('API_KEY') or settings.get('LLM_API_KEY')
    endpoint=settings.get('BASE_URL') or settings.get('LLM_BASE_URL')
    model=settings.get('LLM_NAME') or settings.get('MODEL') or config.LLM_NAME
    if not key or not endpoint:
        raise ValueError('Local real Apertus credentials required')
    dataset_hash=sha(args.dataset.read_bytes())
    nli_hash=sha((config.BASE_DIR/'src/apertus_client.py').read_bytes())
    cache=audited_cache(args.reuse,model,dataset_hash,nli_hash)
    if args.output.exists():
        raise ValueError('Refusing to overwrite previous inference results')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    journal_path=args.output.with_suffix('.journal.jsonl')
    if journal_path.exists():
        raise ValueError('Existing journal: do not repeat paid inference')
    campaign_id=sha(json.dumps([dataset_hash,model,'initial_dev42_2026-10-31']).encode())[:20]
    campaign_path=config.BASE_DIR/'results/genai'/f'nli42_campaign_{campaign_id}.json'
    campaign_path.parent.mkdir(parents=True,exist_ok=True)
    with campaign_path.open('x',encoding='utf-8') as stream:
        json.dump({'max_http_attempts':args.max_requests,'journal':str(journal_path.resolve()),
                   'dataset_sha256':dataset_hash,'created_at':datetime.now(timezone.utc).isoformat()},stream)
        stream.flush();os.fsync(stream.fileno())
    journal=journal_path.open('x',encoding='utf-8')
    budget=AttemptBudget(args.max_requests,journal,max_allowed=126)
    result={'kind':'paired_real_nli_observed_dev','complete':False,'model':model,'mock':False,'prompt':PROMPT,
        'dataset_sha256':dataset_hash,'retrieval_report_sha256':sha(args.report.read_bytes()),
        'runner_sha256':sha(Path(__file__).read_bytes()),'nli_source_sha256':nli_hash,
        'reuse_source_sha256':sha(args.reuse.read_bytes()) if args.reuse else None,
        'expected_case_count':42,'variants':list(VARIANTS),'max_http_attempts':args.max_requests,
        'served_models':[],'rows':[],
        'request_parameters':{'temperature':0.0,'max_tokens':512,'timeout_seconds':45,'automatic_retries':0,'json_repair':False},
        'campaign_file':str(campaign_path),'source_label_counts':dict(Counter(str(r['entailment_label']) for r in source.values())),
        'limitations':['Observed dev/regression on one booklet date, no sealed test or significance claim.',
            'Same constant JSON prompt across variants, not the full production NLI pipeline.',
            'Native baseline retains cue selection and native budget; not an isolated embedding ablation.',
            'Evidence hit uses official fuzzy reference matching; selected Silver witnesses are separate.',
            'Recorded token/latency means include reused responses; new server usage is reported separately.',
            'Quality comparisons use only cases successfully completed by every variant.']}
    client=None
    old_repair=config.LLM_JSON_REPAIR
    billed_in=billed_out=0
    current={}
    errors={}
    consecutive_transient=0
    started=time.perf_counter()
    try:
        budget.append({'kind':'run_header','model':model,'dataset_sha256':dataset_hash,
                       'runner_sha256':result['runner_sha256'],'max_http_attempts':args.max_requests})
        config.LLM_JSON_REPAIR=False
        client=ApertusClient(base_url=endpoint,api_key=key,model_name=model,mock=False)
        client._retry_limit=lambda unused:0
        client._switch_model=lambda unused:False
        connect=client.client._connection
        client.client._connection=lambda timeout:CountedConnection(connect(timeout),budget)
        original_request=client._request_completion
        def request(**kwargs):
            nonlocal billed_in,billed_out
            if kwargs.get('model')!=model:
                raise RuntimeError('Model switching forbidden')
            try:
                response=original_request(**kwargs)
            except Exception as exc:
                current['http_status']=getattr(exc,'status_code',None)
                current['error_type']='http_error' if current['http_status'] else 'transport_error'
                current['fatal']=current['http_status'] in (401,403,404)
                raise
            content=response.choices[0].message.content or ''
            usage=getattr(response,'usage',None)
            pin=getattr(usage,'prompt_tokens',0) or 0
            pout=getattr(usage,'completion_tokens',0) or 0
            billed_in+=pin; billed_out+=pout
            served=getattr(response,'model',None)
            budget.append({'kind':'response_received','sequence':budget.used,'model':served,
                'input_identity':current['identity'],'content':content,
                'finish_reason':getattr(response.choices[0],'finish_reason',None),
                'prompt_tokens':pin,'completion_tokens':pout})
            if served not in result['served_models']:
                result['served_models'].append(served)
            if served!=model:
                current['fatal']=True
                current['error_type']='model_mismatch'
                raise RuntimeError('Served model identity mismatch')
            if getattr(response.choices[0],'finish_reason',None)!='stop':
                current['error_type']='truncated_or_nonstop'
                raise ValueError('Only normally completed responses may be scored')
            current['usage_estimated']=not (type(getattr(usage,'prompt_tokens',None)) is int and
                type(getattr(usage,'completion_tokens',None)) is int)
            current['error_type']='invalid_schema'
            current['raw_label']=validate_response(content)
            current.pop('error_type',None)
            return response
        client._request_completion=request
        for cid,variant,context,frozen in jobs:
            rec=source[cid]
            identity=sha(json.dumps([model,rec['claim'],rec['claim_language'],context],ensure_ascii=False).encode())
            current={'identity':identity}
            if identity not in cache and identity not in errors:
                out=client.infer(context=context,claim=rec['claim'],claim_language=rec['claim_language'])
                if out.error:
                    errors[identity]=current.get('error_type','inference_error_no_label')
                else:
                    cache[identity]={'predicted':out.label,'raw_label':current['raw_label'],
                        'raw_evidence':out.evidence,'decision_rule':out.decision_rule_applied,
                        'prompt_tokens':out.tokens_prompt,'completion_tokens':out.tokens_completion,
                        'latency_ms':out.latency_ms,'usage_estimated':current.get('usage_estimated',True),'origin':'new_remote'}
            row={'id':cid,'variant':variant,'true_label':rec['entailment_label'],
                'pair':rec['claim_language']+'->'+rec['reference_language'],
                'crosslingual':rec['claim_language']!=rec['reference_language'],
                'claim_group':'cue' if claim_part(rec['claim']) else 'free',
                'context_e5_tokens':frozen['context_e5_tokens'],'retrieval_ms':frozen['retrieval_ms'],
                'retrieval_reference_proxy_hit':frozen['context_reference_hit'],
                'retrieval_reference_5gram_recall':frozen['reference_5gram_recall'],
                'input_identity':identity,'error':errors.get(identity),'http_status':current.get('http_status')}
            if identity in errors:
                row.update(predicted=None,raw_label=None)
            else:
                cached=cache[identity]
                row.update(cached)
                row['evidence']=cached['raw_evidence'] if cached['predicted']!=1 else []
                row['evidence_hit_at_5']=evidence_hit5({'evidence':[{'text':e} for e in row['evidence']]},rec['reference_string'])
                if cached['origin']=='new_remote':
                    cache[identity]={**cached,'origin':'within_run_reuse'}
            result['rows'].append(row)
            budget.append({'kind':'result',**row})
            if row['error'] and (row['error']=='transport_error' or row['http_status']==429 or row['http_status'] in (500,502,503,504)):
                consecutive_transient+=1
            else:
                consecutive_transient=0
            if current.get('fatal') or consecutive_transient>=3:
                result['stopped_early']='Systemic endpoint/model failure; no automatic paid retry'
                break
            if len(result['rows'])%3==0:
                print(f"Completed {len(result['rows'])//3}/42 cases; HTTP attempts {budget.used}",flush=True)
    finally:
        config.LLM_JSON_REPAIR=old_repair
        if client is not None:
            client.client._drop()
        result.update(summarize(result['rows'],source))
        result['complete']=result['paired_case_count']==42 and len(result['rows'])==126
        result.update(http_attempts_reserved=budget.used,new_prompt_tokens=billed_in,new_completion_tokens=billed_out,
            wall_seconds=time.perf_counter()-started,error_rows=sum(r['error'] is not None for r in result['rows']))
        with args.output.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        journal.close()
    print(json.dumps({k:result[k] for k in ('complete','paired_case_count','http_attempts_reserved','error_rows','new_prompt_tokens','new_completion_tokens')}))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--reuse',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-requests',type=int,default=126)
    run(parser.parse_args())


if __name__=='__main__':
    main()
