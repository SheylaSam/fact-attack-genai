"""The agent's own MCP server: exactly the three search tools over a `SearchBackend`, spoken over stdio.

Why an own server: no suitable server is known for Swiss voting booklets. `mcp-server-qdrant` has its own embedding
and payload schema; it knows neither the proven passage search (keywords and section logic of the competition path)
nor the bundling of pages per vote. The tool functions are implemented once (`LocalBackend`) and tested without MCP;
this module only puts them behind the protocol.

Scope: `suche_vorlagen`, `suche_passagen` and `suche_passagen_vektor`, plus the resource `vorlagen://katalog`.
`rueckfrage`, `quelle_fehlt` and `pruefen` stay in the graph. The graph sets `behauptung` for `suche_passagen`
(the model never does), so the server declares that parameter itself.

The server never sends an Apertus request: its engine has a mock client, and `server_environment` hands the child
process neither the key file nor any secret. A proven search that needs a translation (claim and booklet in different
languages) is refused with an error result; the request budget counts only requests of the agent process.

stdout carries the protocol. The backend is built with stdout redirected to stderr (Python level and file
descriptor level), so a library that prints during setup cannot corrupt the stream.

Run:  python -m src.agent.mcp_server --index-dir DIR   (from track_2a/)
"""
import argparse
import contextlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from typing import Annotated, Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from src import config
from src.agent import settings
from src.agent.search_backend import LocalBackend, SearchBackend
from src.agent.source_index import SourceIndex
from src.agent.tools import _TOOLS, VECTOR_PASSAGES, VOTE_CANDIDATES, validate_args
from src.apertus_client import ApertusClient
from src.booklet_catalog import load_catalog
from src.inference import ClaimVerificationEngine
from src.text_utils import guess_language

SERVER_NAME = "fact-attack-suche"
VOTES_URI = "vorlagen://katalog"
SEARCH_TOOLS = ("suche_vorlagen", "suche_passagen", "suche_passagen_vektor")
INJECTED_ARGS = {"suche_passagen": ("behauptung",)}  # set by the graph, never by the model

CLAIM_DESCRIPTION = (f"Wortlaut der Behauptung, 1 bis {settings.MAX_CLAIM_CHARS} Zeichen. "
                     "Wird vom Graphen gesetzt, nicht vom Modell.")
CROSS_LINGUAL_ERROR = ("Sprachübergreifende Suche braucht eine Übersetzung durch Apertus; der MCP-Server sendet "
                       "keine Anfragen. Ohne MCP prüfen (--werkzeuge lokal).")
# Variables the server process may inherit. Never passed on: FACT_ATTACK_ENV_FILE, LLM_API_KEY, API_KEY, BASE_URL,
# LLM_BASE_URL and LANGSMITH_* (an allow list, so no other secret can slip through either).
_INHERITED = ("PATH", "HOME", "LANG", "HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "RAG_EMBEDDING_MODEL",
              "RAG_MODEL_REVISION", "AGENT_INDEX_DIR")


def _param_description(tool: str, field: str) -> str:
    return _TOOLS[tool][0].model_json_schema()["properties"][field]["description"]


def _plain(value: Any) -> Any:
    """JSON-safe copy of a result (engine passages may carry numpy scalars or paths)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _checked(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return validate_args(tool, args)
    except ValueError as exc:
        raise ValueError(str(exc)) from None  # an error result for the caller, without the validation traceback


def _known_vote(backend: SearchBackend, vote_id: str):
    vote = backend.votes.get(vote_id)
    if vote is None:
        raise ValueError(f"Unbekannte Vorlagen-ID {vote_id[:80]!r}. Die Vorlagen-IDs stehen in suche_vorlagen "
                         f"und in der Ressource {VOTES_URI}.")
    return vote


def build_server(backend: SearchBackend) -> FastMCP:
    """The FastMCP server for `backend`; every tool validates its arguments itself and fails with a German message."""
    server = FastMCP(SERVER_NAME)

    def suche_vorlagen(
            anfrage: Annotated[str, Field(description=_param_description("suche_vorlagen", "anfrage"))]) -> dict:
        args = _checked("suche_vorlagen", {"anfrage": anfrage})
        candidates = backend.search_votes(args["anfrage"], k=VOTE_CANDIDATES)
        return _plain({"candidates": [
            {"vote_id": c.vote.vote_id, "score": c.score, "hits": c.hits, "best_page": c.best_page,
             "snippet": c.snippet} for c in candidates]})

    def suche_passagen(
            vorlage_id: Annotated[str, Field(description=_param_description("suche_passagen", "vorlage_id"))],
            behauptung: Annotated[str, Field(description=CLAIM_DESCRIPTION)]) -> dict:
        args = _checked("suche_passagen", {"vorlage_id": vorlage_id})
        if not isinstance(behauptung, str) or not behauptung.strip() or len(behauptung) > settings.MAX_CLAIM_CHARS:
            raise ValueError(f"Die Behauptung muss 1 bis {settings.MAX_CLAIM_CHARS} Zeichen lang sein.")
        vote = _known_vote(backend, args["vorlage_id"])
        if guess_language(behauptung) != vote.language:
            raise ValueError(CROSS_LINGUAL_ERROR)
        return _plain({"passages": backend.proven_passages(vote.vote_id, behauptung.strip())})

    def suche_passagen_vektor(
            vorlage_id: Annotated[str, Field(description=_param_description("suche_passagen_vektor", "vorlage_id"))],
            anfrage: Annotated[str, Field(description=_param_description("suche_passagen_vektor", "anfrage"))]) -> dict:
        args = _checked("suche_passagen_vektor", {"vorlage_id": vorlage_id, "anfrage": anfrage})
        vote = _known_vote(backend, args["vorlage_id"])
        return _plain({"passages": backend.vector_passages(vote.vote_id, args["anfrage"], k=VECTOR_PASSAGES)})

    for function in (suche_vorlagen, suche_passagen, suche_passagen_vektor):
        server.add_tool(function, name=function.__name__, description=_TOOLS[function.__name__][1])

    @server.resource(VOTES_URI, name="vorlagen_katalog", mime_type="application/json",
                     description="Alle Vorlagen der Abstimmungsbüchlein mit Vorlagen-ID, Termin, Sprache, Titel "
                                 "und Seiten.")
    def votes_catalog() -> str:
        return json.dumps({"votes": [asdict(vote) for vote in backend.votes.values()]}, ensure_ascii=False)

    return server


@contextlib.contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """stdout (Python object and file descriptor 1) points to stderr, so setup noise never reaches the protocol."""
    sys.stdout.flush()
    saved = os.dup(1)
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        try:
            sys.stderr.flush()
        finally:
            os.dup2(saved, 1)
            os.close(saved)


def serve(make_backend: Callable[[], SearchBackend]) -> None:
    """Build the backend with stdout redirected to stderr, then run the stdio server until the client closes it."""
    with _stdout_to_stderr():
        backend = make_backend()
    build_server(backend).run("stdio")


def make_local_backend(index_dir: Path) -> Tuple[LocalBackend, Callable[[], None]]:
    """Index and engine for the server. The engine's client is a mock: this process never sends a request."""
    index = SourceIndex(load_catalog(config.DATA_DIR / "booklet_catalog.json"), config.BOOKLETS_DIR,
                        index_dir=Path(index_dir).expanduser())
    try:
        engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
    except BaseException:
        index.close()
        raise

    def close() -> None:
        with contextlib.ExitStack() as stack:  # engine first, then the index, even if one of them fails to close
            stack.callback(index.close)
            stack.callback(engine.close)

    return LocalBackend(index, engine, config.BOOKLETS_DIR), close


def server_environment(base: Mapping[str, str] = os.environ) -> Dict[str, str]:
    """The environment of the server process: an allow list of harmless variables, and the mock switch."""
    env = {name: base[name] for name in _INHERITED if name in base}
    env["MOCK_APERTUS"] = "1"
    return env


def server_command(index_dir: Path) -> List[str]:
    return [sys.executable, "-m", "src.agent.mcp_server", "--index-dir", str(index_dir)]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.agent.mcp_server",
                                     description="MCP-Server (stdio) mit den drei Suchwerkzeugen des Agenten.")
    parser.add_argument("--index-dir", type=Path, default=settings.INDEX_DIR, metavar="DIR",
                        help=f"Ordner des Quellenindex (Standard: {settings.INDEX_DIR}).")
    args = parser.parse_args(argv)
    closers: List[Callable[[], None]] = []

    def make_backend() -> SearchBackend:
        backend, close = make_local_backend(args.index_dir)
        closers.append(close)
        return backend

    try:
        serve(make_backend)
        return 0
    except Exception as exc:  # stdout is the protocol: the message goes to stderr
        print(f"MCP-Server beendet: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        for close in closers:
            close()


if __name__ == "__main__":
    sys.exit(main())
