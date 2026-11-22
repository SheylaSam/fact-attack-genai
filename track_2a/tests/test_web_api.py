"""Local API contracts, real offline agent integration, and resumable session lifecycle."""
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src import config
from src.agent.budget import RequestBudget
from src.agent.graph import AgentResult
from src.agent.session import Pending
from src.agent.source_index import VoteRef
from src.web import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, 'DB', tmp_path / 'ui.sqlite3')
    monkeypatch.setattr(config, 'LLM_API_KEY', '')
    with patch('src.apertus_client.ApertusClient._request_completion', side_effect=AssertionError('No model requests')) as remote:
        with TestClient(api.app) as browser:
            yield browser
        remote.assert_not_called()


def finished(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = client.get(f'/api/checks/{identifier}').json()
        if job['status'] != 'running':
            return job
        time.sleep(.02)
    pytest.fail('Worker did not finish in 30 seconds')


def test_offline_real_graph_pdf_and_stream(client):
    sources = client.get('/api/sources').json()
    source = next(s for s in sources if s['language'] == 'de')
    response = client.post('/api/checks', json={'claim': 'Der Bundesrat empfiehlt die Vorlage anzunehmen.',
                                               'source_id': source['id'], 'mode': 'demo'})
    assert response.status_code == 202
    job = finished(client, response.json()['id'])
    assert job['status'] == 'completed'
    assert job['mode'] == 'demo' and job['requests_used'] == 0
    assert job['result']['outcome'] in {'gestuetzt', 'nicht_belegt', 'widersprochen'}
    assert job['result']['evidence_validated']
    assert any(e['node'] == 'verify' for e in job['events'])
    assert client.get(f"/api/sources/{source['id']}/pdf").content.startswith(b'%PDF-')
    stream = client.get(f"/api/checks/{job['id']}/events")
    assert 'event: snapshot' in stream.text and 'event: done' in stream.text
    assert client.delete(f"/api/checks/{job['id']}").status_code == 204
    assert client.get(f"/api/checks/{job['id']}").status_code == 404


def test_config_validation_and_local_origin(client):
    assert client.get('/api/status').json()['api_configured'] is False
    for claim in ('', '   ', 'a' * 1001):
        assert client.post('/api/checks', json={'claim': claim, 'mode': 'demo'}).status_code == 422
    assert client.post('/api/checks', json={'claim': 'Eine Aussage.'}).status_code == 409
    assert client.post('/api/checks', json={'claim': 'Eine Aussage.', 'mode': 'demo', 'max_requests': 13}).status_code == 422
    assert client.post('/api/checks', json={'claim': 'Eine Aussage.', 'mode': 'demo', 'source_id': '../.env'}).status_code == 422
    assert client.post('/api/checks', json={'claim': 'Eine Aussage.', 'mode': 'demo'}, headers={'origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/status', headers={'host': 'evil.example'}).status_code == 400
    assert client.get('/api/sources/unknown/pdf').status_code == 404


def test_invalid_uploads(client):
    assert client.post('/api/uploads', files={'file': ('bad.pdf', b'not a PDF', 'application/pdf')}).status_code == 422
    assert client.post('/api/uploads', files={'file': ('broken.pdf', b'%PDF-broken', 'application/pdf')}).status_code == 422
    assert client.post('/api/uploads', files={'file': ('large.pdf', b'%PDF-' + b'x' * api.MAX_UPLOAD, 'application/pdf')}).status_code == 413


def test_stored_evidence_is_highlighted_yellow_in_original_without_modifying_pdf(client, monkeypatch, tmp_path):
    import base64
    import numpy as np
    import pymupdf
    monkeypatch.setattr(config, 'BOOKLETS_DIR', tmp_path)
    quote = 'Der Bundesrat empfiehlt die Annahme der Vorlage.'
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), quote)
        page.insert_text((50, 80), 'Weitere Informationen stehen im Abstimmungsbuechlein.')
        original = document.tobytes()
    source = client.post('/api/uploads', files={'file': ('beleg.pdf', original, 'application/pdf')}).json()
    job = {'id': 'highlight-proof', 'status': 'completed', 'source_id': source['id'],
           'result': {'outcome': 'gestuetzt', 'evidence_validated': True,
                      'evidence': [{'page': 1, 'quote': quote}]}}
    store = client.app.state.manager.store
    for outcome in ('gestuetzt', 'widersprochen'):
        job['result']['outcome'] = outcome
        store.save(job)
        response = client.get('/api/checks/highlight-proof/evidence/0/preview')
        assert response.status_code == 200
        preview = response.json()
        assert preview['highlighted'] and preview['page'] == 1
        image = pymupdf.Pixmap(base64.b64decode(preview['image'].split(',', 1)[1]))
        pixels = np.frombuffer(image.samples, dtype=np.uint8).reshape(-1, image.n).astype(int)
        assert ((pixels[:, 0] > 180) & (pixels[:, 1] > 170)
                & (pixels[:, 2] < pixels[:, 0] - 20) & (pixels[:, 2] < pixels[:, 1] - 10)).any()
    job['result']['evidence'][0]['focus'] = quote  # the key sentence is marked when it is found on the page
    store.save(job)
    assert client.get('/api/checks/highlight-proof/evidence/0/preview').json()['highlighted'] is True
    job['result']['evidence'][0]['focus'] = 'Nicht auf der Seite.'  # else the whole quote, as before
    store.save(job)
    assert client.get('/api/checks/highlight-proof/evidence/0/preview').json()['highlighted'] is True
    del job['result']['evidence'][0]['focus']
    assert client.get(f"/api/sources/{source['id']}/pdf").content == original
    assert client.get('/api/checks/highlight-proof/evidence/99/preview').status_code == 404
    job['result']['evidence'][0]['quote'] = 'Diese Aussage steht nicht im PDF.'
    store.save(job)
    assert client.get('/api/checks/highlight-proof/evidence/0/preview').json()['highlighted'] is False
    job['result']['evidence_validated'] = False
    store.save(job)
    assert client.get('/api/checks/highlight-proof/evidence/0/preview').status_code == 409


def test_uploaded_document_language_is_separate_from_claim(client, monkeypatch, tmp_path):
    import pymupdf
    monkeypatch.setattr(config, 'BOOKLETS_DIR', tmp_path)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), 'Le Conseil federal et le Parlement recommandent de voter. Les arguments sont dans le texte.')
        content = document.tobytes()
    response = client.post('/api/uploads', files={'file': ('source.pdf', content, 'application/pdf')})
    assert response.status_code == 201
    source = response.json()
    assert source['language'] == 'fr'
    created = client.post('/api/checks', json={'claim': 'Der Bundesrat empfiehlt die Vorlage.',
                                              'language': 'de', 'source_id': source['id'], 'mode': 'demo'})
    assert created.status_code == 202
    job = finished(client, created.json()['id'])
    assert job['language'] == 'de' and job['source_language'] == 'fr'
    assert client.get(f"/api/sources/{source['id']}/pdf").content == content


class FakeEngine:
    closed = False

    def close(self):
        self.closed = True


def test_question_resumes_same_session_and_survives_reload(client, monkeypatch):
    ref = VoteRef('2026-06-14_de#1', '2026-06-14_de', '2026-06-14', 'de', 'Testvorlage', (1,))
    engines, sessions = [], []

    class Session:
        budget = RequestBudget()

        def start(self, *args):
            return Pending('Welche Vorlage?', (ref,), 'guard')

        def answer(self, vote_id):
            assert vote_id == ref.vote_id
            return AgentResult('nicht_belegt', vote=ref)

    def make_session(*args):
        session, engine = Session(), FakeEngine()
        sessions.append(session)
        engines.append(engine)
        return session, engine

    monkeypatch.setattr(api.Runtime, 'session', make_session)
    identifier = client.post('/api/checks', json={'claim': 'Eine Aussage.', 'mode': 'demo'}).json()['id']
    waiting = finished(client, identifier)
    assert waiting['status'] == 'waiting'
    assert client.get('/api/checks').json()[0]['pending']['options'][0]['vote_id'] == ref.vote_id
    assert client.delete(f'/api/checks/{identifier}').status_code == 409
    assert client.post(f'/api/checks/{identifier}/answer', json={'vote_id': 'invalid'}).status_code == 422
    assert client.post(f'/api/checks/{identifier}/answer', json={'vote_id': ref.vote_id}).status_code == 200
    assert finished(client, identifier)['status'] == 'completed'
    assert len(sessions) == 1 and engines[0].closed
    assert client.post(f'/api/checks/{identifier}/answer', json={'vote_id': ref.vote_id}).status_code == 409


def test_cancel_never_becomes_a_verdict_and_late_requests_are_refused(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    budget = RequestBudget()

    class Session:
        def start(self, *args):
            entered.set()
            release.wait(3)
            return AgentResult('gestuetzt')

    session = Session()
    session.budget = budget
    monkeypatch.setattr(api.Runtime, 'session', lambda *args: (session, FakeEngine()))
    identifier = client.post('/api/checks', json={'claim': 'Eine Aussage.', 'mode': 'demo'}).json()['id']
    try:
        assert entered.wait(2)
        assert client.post(f'/api/checks/{identifier}/cancel').json()['status'] == 'cancelled'
        assert budget.cancelled
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while identifier in client.app.state.manager.busy and time.monotonic() < deadline:
        time.sleep(.01)
    job = client.get(f'/api/checks/{identifier}').json()
    assert job['status'] == 'cancelled' and job['result'] is None


def test_restart_marks_unresumable_checks_interrupted(tmp_path):
    path = tmp_path / 'history.sqlite3'
    store = api.Store(path)
    store.save({'id': 'paused', 'status': 'waiting', 'pending': {'question': 'Was?'}, 'mode': 'live'})
    restarted = api.Store(path)
    assert restarted.get('paused')['status'] == 'interrupted'
    assert restarted.get('paused')['pending'] is None
    assert 'neu gestartet' in restarted.get('paused')['error']


def test_evidence_marks_the_key_sentence_and_labels_each_passage():
    from src.web.evidence_focus import annotate
    quote = ('Das Geld wird an alle Rentnerinnen ausbezahlt. Das kostet zu Beginn\nüber 4 Milliarden Franken. '
             'Bereits 2026 gibt die AHV mit der\n13. Rente mehr aus, als sie einnimmt.')
    first, other = annotate('Die 13. AHV-Rente kostet zu Beginn über 4 Milliarden Franken.', 'gestuetzt',
                            [{'page': 4, 'quote': quote}, {'page': 9, 'quote': 'Ein ganz anderes Thema ohne Bezug.'}])
    assert first['focus'] == 'Das kostet zu Beginn\nüber 4 Milliarden Franken.' and first['focus'] in quote
    assert first['stance'] == 'supports' and other == {'page': 9, 'quote': 'Ein ganz anderes Thema ohne Bezug.',
                                                         'focus': None, 'stance': 'neutral'}
    (only,) = annotate('Die Initiative kostet 4 Milliarden.', 'widersprochen', [{'page': 1, 'quote': quote}])
    assert only['stance'] == 'contradicts'


def test_lexical_source_index_finds_the_vote_without_the_dense_stack(monkeypatch):
    from pathlib import Path
    from src.agent.lexical_index import LexicalSourceIndex
    paragraphs = [
        {'page_number': 1, 'text': 'Inhalt: Zivildienstgesetz und Nachhaltigkeitsinitiative.'},
        {'page_number': 2, 'text': 'Wer alle Militärdiensttage geleistet hat, darf nicht mehr in den Zivildienst wechseln.'},
        {'page_number': 3, 'text': 'Der Zivildienst bleibt für Dienstpflichtige offen. Die Zulassung zum Zivildienst wird geprüft.'},
        *({'page_number': 3, 'text': f'Abschnitt {n}: Der Zivildienst dauert länger als der Militärdienst, '
                                     f'die Einsätze im Zivildienst werden geplant.'} for n in range(6)),
        *({'page_number': 4, 'text': f'Teil {n}: Die Zuwanderung in die Schweiz hängt vom Arbeitsmarkt ab, '
                                     f'die Bevölkerung wächst seit Jahren.'} for n in range(6)),
        {'page_number': 4, 'text': 'Die ständige Wohnbevölkerung darf zehn Millionen nicht überschreiten. Das wird schwierig.'},
    ]
    parts = [{'title': 'Änderung des Zivildienstgesetzes', 'parts': {'a': [2, 3]}},
             {'title': 'Nachhaltigkeitsinitiative', 'parts': {'a': [4]}}]
    sources = {'2026-06-14_de.pdf': {'date': '2026-06-14', 'language': 'de', 'votes': []}}
    monkeypatch.setattr('src.agent.lexical_index.available_sources', lambda directory, wanted: wanted)
    index = LexicalSourceIndex(sources, Path('.'), layout=lambda path: (paragraphs, parts))
    zivil = index.search_votes('Der Zivildienst wird schwieriger.')
    assert zivil[0].vote.title == 'Änderung des Zivildienstgesetzes' and zivil[0].score == 1.0
    assert index.search_votes('Die Bevölkerung soll unter zehn Millionen bleiben.')[0].vote.title == 'Nachhaltigkeitsinitiative'
    passages = index.search_passages('2026-06-14_de#1', 'Militärdiensttage', k=5)
    assert [p['page_number'] for p in passages][:2] == [2, 3] and len(passages) == 5  # best first, then the rest; never the title record
    with pytest.raises(ValueError):
        index.search_passages('unbekannt', 'x')


def test_lexical_guard_asks_when_two_booklets_fit_and_shows_where_the_claim_stands(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from src.agent import guard
    from src.agent.lexical_index import LexicalSourceIndex
    from src.web.runtime import turn_payload
    layouts = {
        '2021-03-07_de.pdf': ([{'page_number': 2, 'text': f'{n}: Die E-ID schützt die Daten der Nutzer. Der Bund prüft die Anbieter.'}
                               for n in range(5)] + [{'page_number': 3, 'text': '23 Der Datenschutz im E-ID-Gesetz geht weit.'}],
                              [{'title': 'E-ID-Gesetz', 'parts': {'a': [2, 3]}}]),
        '2025-09-28_de.pdf': ([{'page_number': 4, 'text': f'{n}: Die staatliche E-ID schützt die Daten. Sie ist freiwillig.'}
                               for n in range(5)], [{'title': 'E-ID-Gesetz', 'parts': {'a': [4]}}]),
        '2024-11-24_de.pdf': ([{'page_number': 5, 'text': f'{n}: Die Autobahnen werden ausgebaut. Die Daten zum Verkehr zeigen Staus.'}
                               for n in range(5)], [{'title': 'Ausbau der Nationalstrassen', 'parts': {'a': [5]}}]),
    }
    sources = {name: {'date': name[:10], 'language': 'de', 'votes': []} for name in layouts}
    monkeypatch.setattr('src.agent.lexical_index.available_sources', lambda directory, wanted: wanted)
    index = LexicalSourceIndex(sources, Path('.'), layout=lambda path: layouts[path.name])
    claim = 'Das E-ID-Gesetz schützt die Daten.'
    assessment = guard.assess(index.search_votes(claim), index.guard_thresholds)
    assert assessment.kind == 'mehrdeutig'
    assert set(assessment.close) == {'2021-03-07_de#1', '2025-09-28_de#1'}
    assert guard.assess(index.search_votes('Die Autobahnen werden ausgebaut.'), index.guard_thresholds).kind == 'eindeutig'
    session = SimpleNamespace(tools=SimpleNamespace(backend=SimpleNamespace(index=index)))
    options = tuple(index.votes[vote_id] for vote_id in assessment.close)
    payload = turn_payload(Pending(question='Welche Vorlage?', options=options, asked_by='guard'), claim, session)
    passages = {option['vote_id']: option['passage'] for option in payload['options']}
    assert passages['2021-03-07_de#1'] == {'page': 3, 'excerpt': 'Der Datenschutz im E-ID-Gesetz geht weit.'}  # no '23 '
    assert passages['2025-09-28_de#1']['page'] == 4 and 'schützt die Daten' in passages['2025-09-28_de#1']['excerpt']
    unknown = VoteRef('custom_x#0', 'custom_x', '2026-01-01', 'de', 'Eigenes PDF', (1,))
    assert turn_payload(Pending(question='?', options=(unknown,), asked_by='guard'), claim, session)['options'][0]['passage'] is None
