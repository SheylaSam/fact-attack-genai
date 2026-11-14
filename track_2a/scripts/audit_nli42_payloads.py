"""Rebuild the fixed Dev42 request payload hashes offline; no network/credentials."""
import json
from pathlib import Path
from types import SimpleNamespace
from run_nli42 import prepare_jobs, sha
from src.apertus_client import ApertusClient

root=Path(__file__).resolve().parents[1]
result_path=root/'results/genai/real_nli42.json'
result=json.loads(result_path.read_text())
source,jobs=prepare_jobs(root/'data/genai/ost_june_dev.jsonl',root/'results/genai/nli42_frozen_retrieval.json')
expected={}
captures=[]
client=ApertusClient(mock=True)
# The completion method is fully replaced; no HTTP client is initialized.
client.mock=False
client.model_name=result['model']
def capture(**kwargs):
    kwargs.pop('timeout',None)
    captures.append(kwargs)
    content=json.dumps(dict(label=1,p_entail=0,p_neutral=1,p_contra=0,reasoning='Offline audit',evidence=[]))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason='stop')],
                           usage=SimpleNamespace(prompt_tokens=1,completion_tokens=1))
client._request_completion=capture
for cid,_,context,_ in jobs:
    rec=source[cid]
    client.infer(context=context,claim=rec['claim'],claim_language=rec['claim_language'])
    body=captures[-1]
    identity=sha(json.dumps([result['model'],rec['claim'],rec['claim_language'],context],ensure_ascii=False).encode())
    expected[identity]=sha(json.dumps(body).encode())
    assert body['max_tokens']==512 and body['temperature']==0.0 and body['model']==result['model']
events=[json.loads(x) for x in result_path.with_suffix('.journal.jsonl').read_text().splitlines()]
requests={e['sequence']:e['body_sha256'] for e in events if e['kind']=='request_reserved'}
responses=[e for e in events if e['kind']=='response_received']
for e in responses:
    assert requests[e['sequence']]==expected[e['input_identity']], 'Request bytes differ from rebuilt prompt'
old=[json.loads(x) for x in (root/'results/genai/real_nli_access_smoke.journal.jsonl').read_text().splitlines()]
old_requests={e['sequence']:e['body_sha256'] for e in old if e['kind']=='request_reserved'}
receipts={};pending=None
for e in old:
    if e['kind']=='response_received':pending=e['sequence']
    elif e['kind']=='result' and e['input_identity'] not in receipts and pending is not None:
        receipts[e['input_identity']]=pending;pending=None
reused={r['input_identity'] for r in result['rows'] if r.get('origin')=='audited_prior_smoke'}
for identity in reused:
    assert old_requests[receipts[identity]]==expected[identity], 'Prior payload differs from exact current prompt'
report={'kind':'offline_serialized_payload_hash_audit','network_requests':0,
    'result_sha256':sha(result_path.read_bytes()),'auditor_sha256':sha(Path(__file__).read_bytes()),
    'new_request_hashes_verified':len(responses),'prior_reused_request_hashes_verified':len(reused),
    'unique_inputs_rebuilt':len(expected),'temperature_verified':0.0,'max_tokens_verified':512}
(root/'results/genai/real_nli42_payload_audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
