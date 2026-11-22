"""One bounded, resumable agent session per browser check."""
from dataclasses import asdict
from contextlib import ExitStack
import asyncio
import re
from pathlib import Path
import threading

from src import config
from src.agent import settings
from src.agent.budget import RequestBudget, install_on_apertus
from src.agent.decider import make_decider
from src.agent.search_backend import LocalBackend
from src.agent.session import AgentSession, Pending
from src.agent.source_index import SourceIndex, VoteRef
from src.agent.tools import AgentTools
from src.agent.trace import Tracer
from src.agent.uploads import whole_booklet_vote
from src.apertus_client import ApertusClient
from src.booklet_catalog import available_sources, load_catalog
from src.inference import ClaimVerificationEngine
from src.web.evidence_focus import annotate, focus_sentence

PASSAGE_CHARS = 280  # the excerpt of an option in the question


class FixedSourceIndex:
    """A selected PDF needs no dense index. It is registered only in its own backend."""
    votes = {}


class Runtime:
    def __init__(self):
        self._index = None
        self._index_lock = threading.RLock()

    def catalog(self):
        return available_sources(config.BOOKLETS_DIR, load_catalog(config.DATA_DIR / 'booklet_catalog.json'))

    def dense_available(self):
        from importlib.util import find_spec
        return (settings.INDEX_DIR.is_dir()
                and any(settings.INDEX_DIR.glob('*/votes.json'))
                and all(find_spec(n) is not None for n in ('sentence_transformers', 'qdrant_client')))

    def auto_available(self):
        """The agent finds the source itself: with the dense index if it is built, else with the BM25 index over
        the local booklets in the agent's languages."""
        if self.dense_available():
            return True
        catalog = load_catalog(config.DATA_DIR / 'booklet_catalog.json')
        return any(entry['language'] in settings.LANGUAGES
                   for entry in available_sources(config.BOOKLETS_DIR, catalog).values())

    def index(self):
        with self._index_lock:
            if self._index is None:
                catalog = load_catalog(config.DATA_DIR / 'booklet_catalog.json')
                if self.dense_available():
                    self._index = SourceIndex(catalog, config.BOOKLETS_DIR, index_dir=settings.INDEX_DIR)
                elif self.auto_available():
                    from src.agent.lexical_index import LexicalSourceIndex
                    self._index = LexicalSourceIndex(catalog, config.BOOKLETS_DIR)
                else:
                    raise ValueError('Für die automatische Quellensuche liegt kein deutsches Abstimmungsbüchlein '
                                     'vor. Bitte Büchlein herunterladen oder eine Quelle wählen.')
            return self._index

    def session(self, job, on_event):
        mock = job['mode'] == 'demo'
        if not mock and (not config.LLM_API_KEY or config.LLM_API_KEY == 'your_api_key_here'):
            raise ValueError('Kein API-Schlüssel konfiguriert. API_KEY in .env eintragen oder den Demomodus wählen.')
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=mock))
        resources = ExitStack()
        resources.callback(engine.close)
        try:
            budget = RequestBudget(limit=job['max_requests'])
            if not mock:
                install_on_apertus(engine.client, budget)
            source_id = job.get('source_id')
            # Offline demonstrations also use real PDFs and the existing agent graph.
            if mock and not source_id:
                source_id = next((Path(n).stem for n in sorted(self.catalog(), reverse=True) if n.endswith('_de.pdf')), None)
            selected = None
            if source_id:
                if source_id.startswith('custom_'):
                    from src.agent.uploads import upload_vote
                    selected = upload_vote(config.BOOKLETS_DIR / f'{source_id}.pdf',
                                           job.get('source_language') or job['language'])
                else:
                    entry = self.catalog().get(f'{source_id}.pdf')
                    if entry is None:
                        raise ValueError('Das gewählte Büchlein ist nicht lokal verfügbar.')
                    known = VoteRef(f'{source_id}#0', source_id, entry['date'], entry['language'], 'Gesamtes Büchlein', ())
                    selected = whole_booklet_vote(config.BOOKLETS_DIR, known)
            index = FixedSourceIndex() if selected else self.index()
            backend = LocalBackend(index, engine, config.BOOKLETS_DIR, catalog=self.catalog())
            if selected:
                backend.add_vote(selected)
            fixed_id = selected.vote_id if selected else None
            tools = AgentTools(backend, config.BOOKLETS_DIR, fixed_vote_id=fixed_id)
            decider = make_decider('scripted' if mock else settings.TOOL_MODE, budget, fixed_vote_id=fixed_id)
            model = getattr(decider, 'chat_model', None)
            if model is not None:
                resources.callback(model.http_client.close)
                resources.callback(lambda: asyncio.run(model.http_async_client.aclose()))
            session = AgentSession(tools, decider, engine, Tracer(on_event=on_event), budget=budget)
            return session, resources
        except BaseException:
            resources.close()
            raise

    def close(self):
        with self._index_lock:
            if self._index is not None:
                self._index.close()


def option_passage(session, vote_id, claim):
    """Where the claim stands in one option: the best-matching passage's page and its key sentence, or None."""
    try:
        found = session.tools.backend.index.search_passages(vote_id, claim, k=1)
    except Exception:  # an option the index does not know (e.g. a session vote): shown without passage
        return None
    if not found or not found[0].get('retrieval_score'):
        return None
    text = found[0]['text']
    sentence, _ = focus_sentence(text, claim)
    excerpt = re.sub(r'^\d{1,3}\s+', '', ' '.join((sentence or text).split()))  # page number of the PDF header
    return {'page': found[0]['page_number'],
            'excerpt': excerpt if len(excerpt) <= PASSAGE_CHARS else excerpt[:PASSAGE_CHARS].rsplit(' ', 1)[0] + ' …'}


def turn_payload(turn, claim='', session=None):
    if isinstance(turn, Pending):
        return {'question': turn.question,
                'options': [{**asdict(v), 'passage': option_passage(session, v.vote_id, claim) if session else None}
                            for v in turn.options],
                'asked_by': turn.asked_by}
    prediction = turn.prediction
    return {
        'outcome': turn.outcome, 'reasoning': turn.reasoning, 'note': turn.note, 'error': turn.error,
        'vote': asdict(turn.vote) if turn.vote else None, 'evidence': annotate(claim, turn.outcome, [asdict(e) for e in turn.evidence]),
        'question': turn.question, 'options': [asdict(v) for v in turn.options], 'statements': turn.statements,
        'evidence_validated': turn.evidence_validated, 'checks': turn.checks,
        'metrics': {'input_tokens': sum(e.tokens_prompt for e in turn.events),
                    'output_tokens': sum(e.tokens_completion for e in turn.events),
                    'inference_ms': prediction.latency_ms if prediction else 0},
    }
