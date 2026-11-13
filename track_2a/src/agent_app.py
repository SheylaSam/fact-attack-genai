"""The agent check of the app's fact-check tab, apart from drawing it: whether the agent layer can run, the shared
source index, one session per check, and the check object that outlives a stopped script run.

It lives in a module, not in app.py, so the UI tests can patch `make_agent_session`, `agent_votes` and
`agent_available` (app.py is a script and runs anew on every rerun). The agent layer (langgraph, httpx) is imported
only inside the functions that need it: the app loads without it (`requirements.txt`, the competition lock, has
neither) and then keeps its old flow.
"""
from datetime import datetime, timezone
import importlib
import os
import queue
import threading
from typing import Any, Callable, Iterator, Mapping, Optional, Tuple
import uuid

import streamlit as st

from src import config
from src.agent import settings
from src.agent.trace import TraceEvent
from src.agent_ui import stream

AGENT_SWITCH = "FACT_ATTACK_AGENT"  # "0" keeps the old flow
AGENT_MOCK = "FACT_ATTACK_AGENT_MOCK"  # "1": scripted decider and mock Apertus client, no request leaves the machine


def agent_available() -> bool:
    """The tab runs through the agent: not switched off, the source index exists and langgraph and httpx import."""
    if os.environ.get(AGENT_SWITCH) == "0" or not settings.INDEX_DIR.is_dir():
        return False
    try:
        for name in ("langgraph", "httpx"):
            importlib.import_module(name)
    except ImportError:
        return False
    return True


@st.cache_resource(on_release=lambda index: index.close(), show_spinner=False)
def get_agent_index():
    """The agent's source index, one per app process (the local Qdrant index allows one client)."""
    from src.agent.source_index import SourceIndex
    from src.booklet_catalog import load_catalog
    return SourceIndex(load_catalog(config.DATA_DIR / "booklet_catalog.json"), config.BOOKLETS_DIR,
                       index_dir=settings.INDEX_DIR)


def agent_votes() -> Mapping[str, Any]:
    """vote_id -> VoteRef of the agent's source index."""
    return get_agent_index().votes


def make_agent_session(fixed_vote_id: Optional[str], on_event: Callable[[TraceEvent], None],
                       upload: Optional[Any] = None):
    """A new AgentSession for one check, wired like the CLI (`python -m src.agent`): new tools (an answered check
    leaves its vote fixed in them), a request budget of `settings.MAX_REQUESTS` and a trace in `settings.TRACE_DIR`.

    With `upload` (the `VoteRef` of an own PDF, see `src.agent.uploads.upload_vote`) the session's backend knows that
    booklet too and it is the check's fixed vote, whatever `fixed_vote_id` says; the shared index never learns it.

    The check gets its own engine and Apertus client: the budget is installed on that client only, so the app's shared
    engine (`get_engine()`, used by the old flow and the multi-check) is never patched and one check's count never
    leaks into another. The engine keeps nothing worth sharing for the agent: the parsed booklets are cached
    process-wide, and the agent's passage search is BM25 (`retrieve_passages`), not the engine's vector indexes.
    With `FACT_ATTACK_AGENT_MOCK=1` the scripted decider and the mock client run; otherwise a real client, which
    raises ValueError without an API key."""
    from src.agent.budget import RequestBudget, install_on_apertus
    from src.agent.decider import make_decider
    from src.agent.search_backend import LocalBackend
    from src.agent.session import AgentSession
    from src.agent.tools import AgentTools
    from src.agent.trace import Tracer
    from src.apertus_client import ApertusClient
    from src.inference import ClaimVerificationEngine

    mock = os.environ.get(AGENT_MOCK) == "1"
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-app-{uuid.uuid4().hex[:8]}"
    engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=mock))
    journal = None if mock else settings.TRACE_DIR / f"{run_id}.requests.jsonl"
    budget = RequestBudget(limit=settings.MAX_REQUESTS, journal_path=journal)
    if not mock:
        install_on_apertus(engine.client, budget)  # the NLI client; the agent LLM is counted in make_decider
    backend = LocalBackend(get_agent_index(), engine, config.BOOKLETS_DIR)
    if upload is not None:
        backend.add_vote(upload)
        fixed_vote_id = upload.vote_id
    tools = AgentTools(backend, config.BOOKLETS_DIR, fixed_vote_id=fixed_vote_id)
    decider = make_decider("scripted" if mock else settings.TOOL_MODE, budget, fixed_vote_id=fixed_vote_id)
    tracer = Tracer(settings.TRACE_DIR / f"{run_id}.trace.jsonl", on_event=on_event)
    return AgentSession(tools, decider, engine, tracer, budget=budget)


def is_question(turn: Any) -> bool:
    """The turn is an open question (`Pending`), not the result."""
    from src.agent.session import Pending
    return isinstance(turn, Pending)


def is_discarded(result: Any) -> bool:
    """The verdict was discarded for lack of a checkable quote: the engine's probabilities and its number check
    belong to the dropped label, not to `nicht_belegt`."""
    from src.agent.graph import DISCARDED_NOTE
    return DISCARDED_NOTE in (result.note or "")


class AgentCheck:
    """One check of the tab across script runs: its session (`make_session(fixed_vote_id, on_event)`), the turn its
    worker left and a lock that is held while a worker runs `start` or `answer`.

    A second click while a check runs makes Streamlit stop the script run that started it, but not its worker
    thread. The lock stays held until that worker has ended, so the next run can see it (`running`), wait for it
    (`wait`) and pick up its turn (`take`) instead of sending a second request to the same session."""

    def __init__(self, make_session: Callable[[Optional[str], Callable[[TraceEvent], None]], Any],
                 fixed_vote_id: Optional[str]):
        self.key = uuid.uuid4().hex[:12]  # widget keys of this check's options
        self._events: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._done: Optional[Tuple[str, Any]] = None  # ("turn", turn) or ("error", exception), left by the worker
        self.session = make_session(fixed_vote_id, self._on_event)

    def _on_event(self, event: TraceEvent) -> None:
        self._events.put(event)  # the queue of the current run, looked up per event

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def run(self, call: Callable[[Any], Any]) -> Iterator[TraceEvent]:
        """Runs `call(session)` in a worker thread and yields its events as they come. Yields nothing and runs
        nothing while an earlier call still runs. The turn (or the exception) is left for `take`."""
        if not self._lock.acquire(blocking=False):
            return
        self._events = queue.Queue()  # a stopped run's end marker sits in its own queue, never in this one

        def work() -> None:
            try:
                self._done = ("turn", call(self.session))
            except Exception as exc:  # shown by the app; never lost in the thread
                self._done = ("error", exc)
            finally:
                self._lock.release()

        for item in stream(work, self._events):
            if isinstance(item, TraceEvent):
                yield item

    def wait(self) -> None:
        """Blocks until no worker runs."""
        with self._lock:
            pass

    def take(self) -> Optional[Tuple[str, Any]]:
        """The turn (or exception) the last worker left, once; None while one runs or if there is none."""
        if self.running:
            return None
        done, self._done = self._done, None
        return done
