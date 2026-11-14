"""Local E5/Qdrant plus injected NLI: verify original-source evidence, no API quality score."""
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from src import config
from src.apertus_client import ApertusClient,NLIOutput
from src.inference import ClaimVerificationEngine
from src.official_evaluation import normalize


def run():
    root=Path(__file__).resolve().parents[1]
    pdf=root/'data/booklets/2026-06-14_it.pdf'
    client=ApertusClient(mock=True)
    client.infer=lambda **kwargs:NLIOutput(label=0,reasoning='Injected integration response; not a quality result',
        evidence_ids=[1],evidence=['Traduction francaise artificielle: ne pas exporter.'])
    assert client.mock and client.client is None
    engine=ClaimVerificationEngine(apertus_client=client,prompt_mode='ids')
    started=time.perf_counter()
    report={'kind':'python312_real_embeddings_injected_nli_integration_smoke','nli_mock':True,
        'real_api_calls':0,'quality_metrics':None,'python':sys.version,'platform':platform.platform(),
        'lock_sha256':hashlib.sha256((root/'requirements-genai312-macos-arm64.lock.txt').read_bytes()).hexdigest(),
        'source_sha256':{name:hashlib.sha256((root/'src'/name).read_bytes()).hexdigest()
                         for name in ('inference.py','vector_rag.py','pdf_parser.py','evidence.py')},'variants':[]}
    try:
        with pymupdf.open(pdf) as document:
            original={i+1:normalize(page.get_text(sort=True)) for i,page in enumerate(document)}
        for strategy in ('vector','vector_hybrid'):
            result=engine.verify_claim('Le parlement recommande de rejeter l’initiative.',pdf,
                claim_language='fr',booklet_language='it',strategy=strategy)
            exported=result.to_official_dict()
            assert exported['evidence']
            assert all(normalize(e['text']) in original[e['page']] for e in exported['evidence'])
            assert all('Traduction francaise' not in e['text'] for e in exported['evidence'])
            report['variants'].append({'strategy':strategy,'all_evidence_in_original_source':True,
                'prediction':result.model_dump(),'official_output':exported})
    finally:
        engine.close()
    assert not engine._vector_retrievers
    report['indexes_closed_after_run']=True
    report['wall_ms']=(time.perf_counter()-started)*1000
    output=root/'results/genai/python312_ids_smoke.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Two real embedding strategies, injected NLI, original-source evidence and closed indexes verified')


if __name__=='__main__':
    run()
