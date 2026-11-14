"""Production IDs path never exports the model's translated quotations."""
from pathlib import Path
from unittest.mock import patch
import pytest
import pymupdf
from src import config
from src.apertus_client import ApertusClient,NLIOutput
from src.inference import ClaimVerificationEngine
from src.official_evaluation import normalize

ROOT=Path(__file__).parents[1]


@pytest.mark.parametrize('strategy',['vector','vector_hybrid'])
@pytest.mark.parametrize('label',[0,1,2])
def test_ids_output_is_physical_source_text_even_when_model_quotes_french(strategy,label):
    pdf=ROOT/'data/booklets/2026-06-14_it.pdf'
    client=ApertusClient(mock=True)
    client.infer=lambda **kwargs:NLIOutput(label=label,reasoning='Injected test output',evidence_ids=[1,2],
        evidence=['Le parlement recommande le rejet. Cette citation traduite ne doit pas sortir.'])
    engine=ClaimVerificationEngine(apertus_client=client,prompt_mode='ids')
    # Deliberately use altered retrieved text and duplicate IDs on one physical page.
    passages=[dict(text='Texte de travail traduit',page_number=22,proposal_id=1,section='legal_text') for _ in range(2)]
    try:
        engine._get_booklet_data(pdf)
        with patch.object(config,'A_PROMPT',''),patch.object(config,'TRANSLATE_CLAIM',False), \
             patch.object(config,'EVIDENCE_POLICY','raw_pages_and_blocks'):
            result=engine._compact_predict(passages=passages,claim='Le parlement recommande de rejeter cette initiative.',
                claim_language='fr',vote=None,case_id='synthetic-integration',strategy=strategy,
                booklet_path=str(pdf),numerical_conflict_msg=None,task_a=True)
        exported=result.to_official_dict()
        assert exported['label']==label
        if label==1:
            assert exported['evidence']==[]
        else:
            assert exported['evidence']
            with pymupdf.open(pdf) as document:
                # Production evidence follows physical reading order, not PDF object order.
                original=normalize(document[21].get_text(sort=True))
            assert all(e['page']==22 and normalize(e['text']) in original for e in exported['evidence'])
            assert all('citation traduite' not in e['text'] for e in exported['evidence'])
    finally:
        engine.close()
