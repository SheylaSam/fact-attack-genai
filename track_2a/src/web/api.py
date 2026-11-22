"""Local, single-user API. Run one Uvicorn worker: live agent sessions live in this process."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from typing import Literal

from src import config
from src.agent import settings
from src.agent.session import Pending
from src.web.runtime import Runtime, turn_payload

DB = Path(os.getenv('FACT_ATTACK_UI_DB') or config.DATA_DIR / '.local-history' / 'agent-ui.sqlite3')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
MAX_UPLOAD = 20 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(error):
    message = str(error)[:1000]
    if config.LLM_API_KEY:
        message = message.replace(config.LLM_API_KEY, '[Schlüssel entfernt]')
    return message


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS ui_checks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS ui_uploads (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        # A restart cannot recover LangGraph's in-memory interrupt state. Never repeat paid requests automatically.
        for job in self.all():
            if job['status'] not in TERMINAL:
                job.update(status='interrupted', pending=None,
                           error='Der Server wurde neu gestartet. Bitte die Prüfung erneut starten.')
                self.save(job)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, job):
        payload = json.dumps(job, ensure_ascii=False)
        if config.LLM_API_KEY:
            payload = payload.replace(json.dumps(config.LLM_API_KEY, ensure_ascii=False)[1:-1], '[Schlüssel entfernt]')
        with self.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO ui_checks VALUES (?, ?)',
                               (job['id'], payload))

    def all(self):
        with self.connect() as connection:
            return [json.loads(r[0]) for r in connection.execute('SELECT payload FROM ui_checks ORDER BY rowid DESC')]

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute('SELECT payload FROM ui_checks WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Diese Prüfung wurde nicht gefunden.')
        return json.loads(row[0])

    def delete(self, identifier):
        with self.connect() as connection:
            connection.execute('DELETE FROM ui_checks WHERE id=?', (identifier,))

    def uploads(self):
        with self.connect() as connection:
            return [json.loads(r[0]) for r in connection.execute('SELECT payload FROM ui_uploads')]

    def upload(self, record):
        with self.connect() as connection:
            connection.execute('INSERT OR REPLACE INTO ui_uploads VALUES (?,?)',
                               (record['id'], json.dumps(record, ensure_ascii=False)))


class CheckInput(BaseModel):
    claim: str = Field(min_length=1, max_length=settings.MAX_CLAIM_CHARS)
    source_id: str | None = Field(default=None, max_length=100)
    language: Literal['de', 'fr', 'it'] = 'de'
    mode: Literal['live', 'demo'] = 'live'
    max_requests: int = Field(default=settings.MAX_REQUESTS, ge=1, le=settings.MAX_REQUESTS)


class AnswerInput(BaseModel):
    vote_id: str = Field(min_length=1, max_length=120)


class Manager:
    def __init__(self, store, runtime):
        self.store, self.runtime = store, runtime
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fact-check')
        self.lock = threading.RLock()
        self.sessions = {}
        self.busy = set()

    def create(self, values):
        claim = values.claim.strip()
        if not claim:
            raise HTTPException(422, 'Bitte eine Behauptung eingeben.')
        if values.mode == 'live' and (not config.LLM_API_KEY or config.LLM_API_KEY == 'your_api_key_here'):
            raise HTTPException(409, 'API_KEY fehlt in .env. Alternativ kannst du den Demomodus verwenden.')
        if values.mode == 'live' and not values.source_id and not self.runtime.auto_available():
            raise HTTPException(409, 'Bitte ein Büchlein wählen. Der automatische Suchindex ist noch nicht eingerichtet.')
        known_sources = {Path(n).stem: entry for n, entry in self.runtime.catalog().items()}
        known_sources.update({u['id']: u for u in self.store.uploads()})
        allowed = set(known_sources)
        if values.source_id and values.source_id not in allowed:
            raise HTTPException(422, 'Unbekannte oder nicht verfügbare Quelle.')
        with self.lock:
            if len(self.busy) >= 2:
                raise HTTPException(429, 'Es laufen bereits zwei Prüfungen. Bitte kurz warten.')
            if sum(j['status'] == 'waiting' for j in self.store.all()) >= 20:
                raise HTTPException(429, 'Bitte zuerst offene Rückfragen beantworten oder Prüfungen abbrechen.')
            job = {**values.model_dump(), 'claim': claim, 'id': uuid4().hex, 'created_at': now(),
                   'source_language': (known_sources.get(values.source_id) or {}).get('language'),
                   'status': 'running', 'events': [], 'result': None, 'pending': None, 'error': None,
                   'requests_used': 0, 'elapsed_ms': 0}
            self.store.save(job)
            self.busy.add(job['id'])
            self.pool.submit(self.work, job['id'])
            return job

    def event(self, identifier, event):
        with self.lock:
            job = self.store.get(identifier)
            job['events'].append(asdict(event))
            session = self.sessions.get(identifier)
            if session:
                job['requests_used'] = session[0].budget.used
            self.store.save(job)

    def work(self, identifier, answer=None):
        started = time.monotonic()
        session = engine = None
        try:
            job = self.store.get(identifier)
            if answer is None:
                session, engine = self.runtime.session(job, lambda e: self.event(identifier, e))
                with self.lock:
                    self.sessions[identifier] = (session, engine)
                    if self.store.get(identifier)['status'] == 'cancelled':
                        session.budget.cancel()
                call = lambda: session.start(job['claim'], job['language'])
            else:
                session, engine = self.sessions[identifier]
                call = lambda: session.answer(answer)
            # A local Qdrant instance and its encoder are shared; only automatic searches take this lock.
            if not job.get('source_id') and job['mode'] == 'live':
                with self.runtime._index_lock:
                    turn = call()
            else:
                turn = call()
            payload = turn_payload(turn, job['claim'], session)
            with self.lock:
                job = self.store.get(identifier)
                job['requests_used'] = session.budget.used
                if job['status'] != 'cancelled':
                    if isinstance(turn, Pending):
                        job.update(status='waiting', pending=payload)
                    else:
                        job.update(status='failed' if turn.outcome == 'technischer_fehler' else 'completed',
                                   result=payload, pending=None)
                self.store.save(job)
        except Exception as exc:
            with self.lock:
                job = self.store.get(identifier)
                if job['status'] != 'cancelled':
                    job.update(status='failed', error=safe_error(exc), pending=None)
                    self.store.save(job)
        finally:
            with self.lock:
                job = self.store.get(identifier)
                job['elapsed_ms'] += round((time.monotonic() - started) * 1000)
                self.store.save(job)
                self.busy.discard(identifier)
                if job['status'] != 'waiting':
                    self.sessions.pop(identifier, None)
                    if engine:
                        engine.close()

    def answer(self, identifier, vote_id):
        with self.lock:
            job = self.store.get(identifier)
            if identifier in self.busy or job['status'] != 'waiting' or identifier not in self.sessions:
                raise HTTPException(409, 'Diese Prüfung wartet gerade nicht auf eine Antwort.')
            if len(self.busy) >= 2:
                raise HTTPException(429, 'Es laufen bereits zwei Prüfungen. Bitte kurz warten.')
            choices = {o['vote_id'] for o in job['pending']['options']} | {'keine'}
            if vote_id not in choices:
                raise HTTPException(422, 'Bitte eine der angebotenen Vorlagen wählen.')
            job.update(status='running', pending=None)
            self.store.save(job)
            self.busy.add(identifier)
            self.pool.submit(self.work, identifier, vote_id)
            return job

    def cancel(self, identifier):
        with self.lock:
            job = self.store.get(identifier)
            if job['status'] in TERMINAL:
                return job
            if identifier in self.sessions:
                session, engine = self.sessions[identifier]
                session.budget.cancel()
                if identifier not in self.busy:
                    engine.close()
                    self.sessions.pop(identifier)
            job.update(status='cancelled', pending=None)
            self.store.save(job)
            return job

    def close(self):
        with self.lock:
            for session, _ in self.sessions.values():
                session.budget.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
        for _, engine in self.sessions.values():
            engine.close()
        self.runtime.close()


@asynccontextmanager
async def lifespan(app):
    app.state.manager = Manager(Store(DB), Runtime())
    yield
    await asyncio.to_thread(app.state.manager.close)


app = FastAPI(title='Fact Attack Local API', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != 'http' or parsed.netloc not in {'localhost:5173', '127.0.0.1:5173',
                                                               'localhost:8000', '127.0.0.1:8000'}:
                return JSONResponse({'detail': 'Zugriff nur aus der lokalen Oberfläche erlaubt.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def manager(request):
    return request.app.state.manager


@app.get('/api/status')
def status(request: Request):
    return {'name': 'Fact Attack', 'model': config.LLM_NAME,
            'api_configured': bool(config.LLM_API_KEY and config.LLM_API_KEY != 'your_api_key_here'),
            'auto_available': manager(request).runtime.auto_available(), 'max_claim_chars': settings.MAX_CLAIM_CHARS,
            'max_requests': settings.MAX_REQUESTS, 'max_upload_mb': MAX_UPLOAD // 1024 // 1024}


@app.get('/api/sources')
def sources(request: Request):
    m = manager(request)
    official = [{'id': Path(n).stem, 'name': n, 'kind': 'official', **entry}
                for n, entry in sorted(m.runtime.catalog().items(), reverse=True)]
    return official + m.store.uploads()


@app.post('/api/uploads', status_code=201)
async def upload(request: Request, file: UploadFile, language: Literal['de', 'fr', 'it'] | None = None):
    from src.agent.uploads import upload_vote
    from src.ui_strategies import store_uploaded_source
    try:
        data = await file.read(MAX_UPLOAD + 1)
    finally:
        await file.close()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, 'Das PDF darf höchstens 20 MB gross sein.')
    if not data.startswith(b'%PDF-'):
        raise HTTPException(422, 'Bitte eine gültige PDF-Datei hochladen.')
    def save():
        path = store_uploaded_source(config.BOOKLETS_DIR, data)
        try:
            ref = upload_vote(path, language or 'de')
            detected = language
            if detected is None:
                import pymupdf
                from src.text_utils import guess_language
                with pymupdf.open(path) as document:
                    detected = guess_language(' '.join(document[i].get_text()[:6000]
                                                       for i in range(min(3, len(document)))))
        except (ValueError, RuntimeError) as exc:
            path.unlink(missing_ok=True)
            raise HTTPException(422, safe_error(exc)) from None
        record = {'id': ref.source_id, 'name': Path(file.filename or 'Eigenes PDF').name[:120],
                  'kind': 'upload', 'date': '', 'language': detected, 'votes': [], 'url': None}
        manager(request).store.upload(record)
        return record
    return await asyncio.to_thread(save)


@app.get('/api/sources/{source_id}/pdf')
def pdf(source_id: str, request: Request):
    if source_id not in {s['id'] for s in sources(request)}:
        raise HTTPException(404, 'Quelle nicht gefunden.')
    path = config.BOOKLETS_DIR / f'{source_id}.pdf'
    if not path.is_file():
        raise HTTPException(404, 'Das PDF ist nicht mehr verfügbar.')
    return FileResponse(path, media_type='application/pdf', content_disposition_type='inline', filename=path.name)


@app.get('/api/checks')
def checks(request: Request):
    return manager(request).store.all()


@app.post('/api/checks', status_code=202)
def create_check(values: CheckInput, request: Request):
    return manager(request).create(values)


@app.get('/api/checks/{identifier}')
def check(identifier: str, request: Request):
    return manager(request).store.get(identifier)


@app.get('/api/checks/{identifier}/evidence/{index}/preview')
def evidence_preview(identifier: str, index: int, request: Request):
    """Render the stored, validated quote in yellow; no arbitrary path or quote from the browser."""
    from src.pdf_preview import render_evidence_page
    job = manager(request).store.get(identifier)
    result = job.get('result') or {}
    if (job.get('status') != 'completed' or result.get('outcome') not in {'gestuetzt', 'widersprochen'}
            or result.get('evidence_validated') is not True):
        raise HTTPException(409, 'Für diese Prüfung gibt es keinen validierten Originalbeleg.')
    evidence = result.get('evidence') or []
    if index < 0 or index >= len(evidence):
        raise HTTPException(404, 'Diese Belegstelle wurde nicht gefunden.')
    source_id = (result.get('vote') or {}).get('source_id') or job.get('source_id')
    if source_id not in {source['id'] for source in sources(request)}:
        raise HTTPException(404, 'Die Originalquelle ist nicht mehr verfügbar.')
    item = evidence[index]
    if type(item.get('page')) is not int or not isinstance(item.get('quote'), str):
        raise HTTPException(422, 'Die gespeicherte Belegstelle ist ungültig.')
    try:
        data = (config.BOOKLETS_DIR / f'{source_id}.pdf').read_bytes()
        label = 0 if result['outcome'] == 'gestuetzt' else 2
        focus = item.get('focus')
        preview = None
        if isinstance(focus, str) and focus.strip():  # mark only the sentence the card marks, if it is unique
            preview = render_evidence_page(data, item['page'], focus, label, highlight_color=(1.0, 0.88, 0.18))
        if preview is None or not preview.highlighted:
            preview = render_evidence_page(data, item['page'], item['quote'], label,
                                           highlight_color=(1.0, 0.88, 0.18))
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(422, 'Die Belegseite kann gerade nicht dargestellt werden.') from None
    return {'page': item['page'], 'highlighted': preview.highlighted, 'reason': preview.reason,
            'image': 'data:image/png;base64,' + base64.b64encode(preview.png).decode('ascii')}


@app.post('/api/checks/{identifier}/answer')
def answer(identifier: str, values: AnswerInput, request: Request):
    return manager(request).answer(identifier, values.vote_id)


@app.post('/api/checks/{identifier}/cancel')
def cancel(identifier: str, request: Request):
    return manager(request).cancel(identifier)


@app.delete('/api/checks/{identifier}', status_code=204)
def delete(identifier: str, request: Request):
    m = manager(request)
    with m.lock:
        job = m.store.get(identifier)
        if identifier in m.busy or job['status'] not in TERMINAL:
            raise HTTPException(409, 'Bitte die laufende Prüfung zuerst abbrechen und kurz warten.')
        m.store.delete(identifier)
    return Response(status_code=204)


@app.get('/api/checks/{identifier}/events')
async def events(identifier: str, request: Request):
    m = manager(request)
    m.store.get(identifier)
    async def stream():
        last = None
        while not await request.is_disconnected():
            job = await asyncio.to_thread(m.store.get, identifier)
            payload = json.dumps(job, ensure_ascii=False)
            if payload != last:
                yield f'event: snapshot\ndata: {payload}\n\n'
                last = payload
            if job['status'] != 'running':
                yield 'event: done\ndata: {}\n\n'
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(0.4)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


DIST = config.REPO_ROOT / 'frontend' / 'dist'
if DIST.is_dir():
    app.mount('/', StaticFiles(directory=DIST, html=True), name='frontend')
