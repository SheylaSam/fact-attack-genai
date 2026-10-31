"""Bounded real NLI access smoke on frozen dev contexts, never a quality study.

The source .env is read in memory. Every HTTP POST attempt is reserved before send;
reconnects count, model switching/LLM retries/JSON repair are disabled.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
from src import config
from src.apertus_client import ApertusClient
from src.evaluator import _macro_f1
from src.official_evaluation import evidence_hit5


def sha(value):
    return hashlib.sha256(value).hexdigest()


def strict_label(content):
    if '<|inner_prefix|>' in content and '<|inner_suffix|>' not in content:
        raise ValueError('Incomplete thinking output')
    content=content.rsplit('<|inner_suffix|>',1)[-1].strip()
    if content.startswith('```') and content.endswith('```'):
        content=re.sub(r'^```(?:json)?\s*','',content)[:-3].strip()
    parsed=json.loads(content)
    if not isinstance(parsed,dict) or type(parsed.get('label')) is not int or parsed['label'] not in (0,1,2):
        raise ValueError('Explicit valid integer label required in complete JSON')
    return parsed['label']


class AttemptBudget:
    def __init__(self, limit, journal):
        if type(limit) is not int or not 1<=limit<=12:
            raise ValueError('HTTP budget must be 1 through 12')
        self.limit, self.used, self.journal = limit, 0, journal
    def append(self, event):
        self.journal.write(json.dumps(event,ensure_ascii=False)+'\n')
        self.journal.flush()
        os.fsync(self.journal.fileno())
    def reserve(self, method, body):
        if method != 'POST' or self.used >= self.limit:
            raise RuntimeError('HTTP attempt budget exhausted or unexpected method')
        self.used += 1
        self.append({'kind':'request_reserved','sequence':self.used,
                     'body_sha256':sha(body if isinstance(body,bytes) else str(body).encode()),
                     'timestamp':datetime.now(timezone.utc).isoformat()})


class CountedConnection:
    def __init__(self, connection, budget):
        self.connection, self.budget = connection, budget
    def request(self, method, url, body=None, headers=None, **kwargs):
        self.budget.reserve(method,body)
        return self.connection.request(method,url,body=body,headers=headers or {},**kwargs)
    def __getattr__(self,name):
        return getattr(self.connection,name)


def run(args):
    dataset_bytes=args.dataset.read_bytes()
    source={r['id']:r for r in map(json.loads,dataset_bytes.decode().splitlines())}
    report=json.loads(args.report.read_text())
    if sha(dataset_bytes)!=report['dataset_sha256']:
        raise ValueError('Frozen report/dataset mismatch')
    chosen=args.case_ids
    if len(chosen)!=len(set(chosen)) or not set(chosen).issubset(source):
        raise ValueError('Invalid case subset')
    variants=sorted({r['variant'] for r in report['rows']})
    if len(chosen)!=2 or len(variants)!=6 or {source[cid]['entailment_label'] for cid in chosen}!={0,2}:
        raise ValueError('Access smoke requires two cases, one Entailment/one Contradiction, across six variants')
    jobs=[]
    for cid in chosen:
        for variant in variants:
            matches=[r for r in report['rows'] if r['id']==cid and r['variant']==variant]
            if len(matches)!=1 or 'context_hits' not in matches[0]:
                raise ValueError('Require exactly one frozen context per case and variant')
            hits=matches[0]['context_hits']
            if any(sha(h['text'].encode())!=h['text_sha256'] for h in hits):
                raise ValueError('Frozen text hash mismatch')
            context='\n\n'.join(f"[Page {h['page_number']}] {h['text']}" for h in hits)
            jobs.append((cid,variant,context))
    settings=dotenv_values(args.env_file)
    key=settings.get('API_KEY') or settings.get('LLM_API_KEY')
    endpoint=settings.get('BASE_URL') or settings.get('LLM_BASE_URL')
    model=settings.get('LLM_NAME') or settings.get('MODEL') or config.LLM_NAME
    if not key or not endpoint:
        raise ValueError('Apertus key and endpoint required in supplied local .env')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    journal_path=args.output.with_suffix('.journal.jsonl')
    # Refuse an accidental second paid run. Interrupted reservations must be reviewed manually.
    if args.output.exists():
        raise ValueError('Refusing to overwrite existing paid-run output')
    journal=journal_path.open('x',encoding='utf-8')
    result={'kind':'real_fixed_context_access_smoke_not_quality_study','complete':False,
        'model':model,'mock':False,'case_ids':chosen,'variants':variants,
        'max_http_attempts':args.max_requests,'dataset_sha256':sha(dataset_bytes),
        'retrieval_report_sha256':sha(args.report.read_bytes()),'runner_sha256':sha(Path(__file__).read_bytes()),
        'nli_source_sha256':sha((config.BASE_DIR/'src/apertus_client.py').read_bytes()),
        'prompt':'Constant ApertusClient.infer JSON, raw Page joins, not production NLI pipeline',
        'limitations':['Two selected dev cases cannot establish a model ranking or reliable macro-F1.',
            'No Neutral case in the default pair; all three-label macro-F1 is only a smoke diagnostic.',
            'Identical claim/context jobs reuse one response across variants; billed usage counts unique calls.'],
        'rows':[],'served_models':[]}
    client=None
    previous_repair=config.LLM_JSON_REPAIR
    budget=AttemptBudget(args.max_requests,journal)
    cache={}
    billed_in=billed_out=0
    try:
        config.LLM_JSON_REPAIR=False
        client=ApertusClient(base_url=endpoint,api_key=key,model_name=model,mock=False)
        client._retry_limit=lambda unused:0
        client._switch_model=lambda unused:False
        original_connection=client.client._connection
        client.client._connection=lambda timeout:CountedConnection(original_connection(timeout),budget)
        original_request=client._request_completion
        raw_label_holder={}
        def request(**kwargs):
            nonlocal billed_in,billed_out
            if kwargs.get('model')!=model:
                raise RuntimeError('Model switch refused')
            response=original_request(**kwargs)
            content=response.choices[0].message.content or ''
            usage=getattr(response,'usage',None)
            prompt_tokens=getattr(usage,'prompt_tokens',0) or 0
            completion_tokens=getattr(usage,'completion_tokens',0) or 0
            billed_in+=prompt_tokens; billed_out+=completion_tokens
            served=getattr(response,'model',None)
            budget.append({'kind':'response_received','sequence':budget.used,'model':served,
                'content':content,'finish_reason':getattr(response.choices[0],'finish_reason',None),
                'prompt_tokens':prompt_tokens,'completion_tokens':completion_tokens})
            if served and served not in result['served_models']:
                result['served_models'].append(served)
            if served and served!=model:
                raise RuntimeError('Served model differs from configured model')
            raw_label_holder['label']=strict_label(content)
            return response
        client._request_completion=request
        for cid,variant,context in jobs:
            rec=source[cid]
            identity=sha(json.dumps([model,rec['claim'],rec['claim_language'],context],ensure_ascii=False).encode())
            reused=identity in cache
            if reused:
                out,raw_label=cache[identity]
            else:
                out=client.infer(context=context,claim=rec['claim'],claim_language=rec['claim_language'])
                if out.error:
                    budget.append({'kind':'inference_error','id':cid,'variant':variant,'identity':identity})
                    result['error']='Inference failed; inspect access/model/response before any new paid run'
                    break
                raw_label=raw_label_holder['label']
                cache[identity]=(out,raw_label)
            row={'id':cid,'variant':variant,'true_label':rec['entailment_label'],'predicted':out.label,'raw_label':raw_label,
                 'evidence':out.evidence,'decision_rule':out.decision_rule_applied,
                 'prompt_tokens':out.tokens_prompt,'completion_tokens':out.tokens_completion,
                 'latency_ms':out.latency_ms,'response_reused':reused,'input_identity':identity,
                 'evidence_hit_at_5':evidence_hit5({'evidence':[{'text':e} for e in out.evidence]},rec['reference_string'])}
            result['rows'].append(row)
            budget.append({'kind':'result',**row})
        result['complete']=len(result['rows'])==len(jobs)
        if result['complete']:
            result['summary']=[]
            for variant in variants:
                rows=[r for r in result['rows'] if r['variant']==variant]
                eligible=[r for r in rows if r['true_label']!=1]
                result['summary'].append({'variant':variant,'count':len(rows),
                    'macro_f1_smoke_only':_macro_f1([r['true_label'] for r in rows],[r['predicted'] for r in rows]),
                    'correct':sum(r['true_label']==r['predicted'] for r in rows),
                    'evidence_hit_at_5':sum(r['evidence_hit_at_5'] for r in eligible)/len(eligible) if eligible else None})
    finally:
        config.LLM_JSON_REPAIR=previous_repair
        if client is not None:
            client.client._drop()
        result.update(http_attempts_reserved=budget.used,unique_successful_inferences=len(cache),
                      billed_prompt_tokens=billed_in,billed_completion_tokens=billed_out)
        with args.output.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        journal.close()
    print(json.dumps({k:result[k] for k in ('complete','http_attempts_reserved','unique_successful_inferences','billed_prompt_tokens','billed_completion_tokens')}))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-ids',nargs='+',default=['ost-0000','ost-1208'])
    parser.add_argument('--max-requests',type=int,default=12)
    args=parser.parse_args()
    if not 1<=args.max_requests<=12:
        parser.error('This access smoke permits 1 through 12 HTTP attempts')
    run(args)


if __name__=='__main__':
    main()
