"""The agent's own MCP server: exactly the three search tools over a `SearchBackend`, spoken over stdio.

Why an own server: no suitable server is known for Swiss voting booklets. `mcp-server-qdrant` has its own embedding
and payload schema; it knows neither the proven passage search (keywords and section logic of the competition path)
nor the bundling of pages per vote. The tool functions are implemented once (`LocalBackend`) and tested without MCP;
this module only puts them behind the protocol.

Scope: `suche_vorlagen`, `suche_passagen` and `suche_passagen_vektor`, plus the resource `vorlagen://katalog`.
`rueckfrage`, `quelle_fehlt` and `pruefen` stay in the graph. The graph sets `behauptung` for `suche_passagen`
(the model never does), so the server declares that parameter itself.

The server never sends an Apertus request: its engine has a mock client, and `server_environment` hands the child
process only an allow list of harmless variables plus `MOCK_APERTUS=1` and `FACT_ATTACK_SKIP_DOTENV=1`, so
`src.config` loads no `.env` file in the child (neither the key file nor `track_2a/.env`, `<repo>/.env` or one found by
searching upwards) and no secret reaches the process. A proven search that needs a translation (claim and booklet in different
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
from src.agent.tools import (_TOOLS, MAX_ID_CHARS, MAX_QUERY_CHARS, VECTOR_PASSAGES, VOTE_CANDIDATES,
                             validate_args)
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
# Variables the server process may inherit (an allow list, so no other secret can slip through): the process basics, the
# model and index locations, and every non-secret setting `src/config.py` reads. The latter must match the agent's
# own values: src.config reads os.environ (load_dotenv has already put the agent's .env values there), while the child
# loads no .env file, so a setting that is not forwarded would silently differ between the two processes. TMPDIR keeps
# the caches (booklets, query translations) in the same place.
_PROCESS_VARIABLES = ("PATH", "HOME", "LANG", "TMPDIR", "HF_HOME", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                      "RAG_EMBEDDING_MODEL", "RAG_MODEL_REVISION", "AGENT_INDEX_DIR")
_CONFIG_VARIABLES = (
    "LLM_NAME", "MODEL", "LLM_STREAMING", "LLM_REQUEST_TIMEOUT_S", "LLM_JSON_REPAIR", "NLI_OFFICIAL_IO",
    "LLM_MAX_RETRIES", "LLM_RETRY_BUDGET_S", "NLI_STRATEGY", "NLI_TOP_K", "PASSAGE_CHARS", "PAGE_MAX_CHARS",
    "PAGE_MAX_CHARS_TAIL", "PAGE_FULL_RANKS", "PART_CONTEXT", "PART_CONTEXT_CHARS", "PART_CONTEXT_CHARS_LEGAL",
    "QUICK_PARTS", "B_LABEL_ONLY", "B_PROMPT", "B_BIAS", "B_PROBS_JOURNAL_PATH", "B_ESCALATE_BELOW", "B_SHORT_CHARS",
    "PROMPT_LEAN", "PROMPT_MODE", "IDS_REASON", "A_RELATION_PROMPT", "A_SKILL_PROMPT", "A_COMPACT_PROMPT",
    "A_PROMPT_DROP", "A_PART_LABEL_ONCE", "A_PROMPT", "A_PROBS_JOURNAL_PATH", "PROMPT_INPUT_JOURNAL_PATH",
    "PROMPT_SHORT", "FEW_SHOT", "THINKING", "THINKING_MAX_TOKENS", "THINKING_BUDGET", "TRANSLATE_CLAIM",
    "SPEAKER_AWARE", "SPEAKER_BOOST", "LEGAL_TEXT_MAX_PAGES", "REFERENCE_MAX_CHARS", "REFERENCE_MID_CHARS",
    "REFERENCE_LONG_CHARS", "SPEAKER_HINT", "NUMERIC_OVERRIDE", "EVIDENCE_POLICY", "CACHE_SINGLE_FLIGHT",
    "SOURCE_PAGES_LAZY", "RETRIEVAL_QUERY_MODE", "QUERY_TRANSLATION_CACHE_DIR", "BOOKLET_CACHE_DIR",
    "BOOKLET_CACHE_PREBUILT", "EVIDENCE_SPLIT", "EVIDENCE_FILL", "EVIDENCE_MAX_ITEMS", "EVIDENCE_MAX_CHARS",
    "EVIDENCE_ON_NEUTRAL", "NLI_BATCH_WORKERS", "NLI_WORKERS")
_INHERITED = _PROCESS_VARIABLES + _CONFIG_VARIABLES
# Never passed on: the key file and the secrets (API_KEY, LLM_API_KEY), the endpoint (BASE_URL, LLM_BASE_URL) and
# every LANGSMITH_* name.
_SECRET_VARIABLES = frozenset({"FACT_ATTACK_ENV_FILE", "API_KEY", "LLM_API_KEY", "BASE_URL", "LLM_BASE_URL"})
_SET_BY_SERVER = frozenset({"MOCK_APERTUS", "FACT_ATTACK_SKIP_DOTENV"})  # config names `server_environment` fixes itself


def _param_description(tool: str, field: str) -> str:
    return _TOOLS[tool][0].model_json_schema()["properties"][field]["description"]


def _query_field(tool: str) -> Any:
    """The `anfrage` parameter with the local constraints (1 to MAX_QUERY_CHARS), so the schema equals the local one."""
    return Field(description=_param_description(tool, "anfrage"), min_length=1, max_length=MAX_QUERY_CHARS)


def _vote_field(tool: str) -> Any:
    return Field(description=_param_description(tool, "vorlage_id"), min_length=1, max_length=MAX_ID_CHARS)


def _json_default(value: Any) -> Any:
    """numpy scalars become Python numbers (a page number must stay a number for the agent's page filter); any
    other object json cannot write (a path, say) becomes text."""
    if getattr(value, "ndim", None) == 0 and callable(getattr(value, "item", None)):
        return value.item()
    return str(value)


def _plain(value: Any) -> Any:
    """JSON-safe copy of a result (engine passages may carry numpy scalars or paths)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


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

    def suche_vorlagen(anfrage: Annotated[str, _query_field("suche_vorlagen")]) -> dict:
        args = _checked("suche_vorlagen", {"anfrage": anfrage})
        candidates = backend.search_votes(args["anfrage"], k=VOTE_CANDIDATES)
        return _plain({"candidates": [
            {"vote_id": c.vote.vote_id, "score": c.score, "hits": c.hits, "best_page": c.best_page,
             "snippet": c.snippet} for c in candidates]})

    def suche_passagen(
            vorlage_id: Annotated[str, _vote_field("suche_passagen")],
            behauptung: Annotated[str, Field(description=CLAIM_DESCRIPTION)]) -> dict:
        args = _checked("suche_passagen", {"vorlage_id": vorlage_id})
        if not 1 <= len(behauptung.strip()) <= settings.MAX_CLAIM_CHARS:  # pydantic has already checked the type
            raise ValueError(f"Die Behauptung muss 1 bis {settings.MAX_CLAIM_CHARS} Zeichen lang sein.")
        vote = _known_vote(backend, args["vorlage_id"])
        if guess_language(behauptung) != vote.language:
            raise ValueError(CROSS_LINGUAL_ERROR)
        # unchanged, as AgentTools passes it locally, so MCP and local results agree
        return _plain({"passages": backend.proven_passages(vote.vote_id, behauptung)})

    def suche_passagen_vektor(
            vorlage_id: Annotated[str, _vote_field("suche_passagen_vektor")],
            anfrage: Annotated[str, _query_field("suche_passagen_vektor")]) -> dict:
        args = _checked("suche_passagen_vektor", {"vorlage_id": vorlage_id, "anfrage": anfrage})
        vote = _known_vote(backend, args["vorlage_id"])
        return _plain({"passages": backend.vector_passages(vote.vote_id, args["anfrage"], k=VECTOR_PASSAGES)})

    for function in (suche_vorlagen, suche_passagen, suche_passagen_vektor):
        server.add_tool(function, name=function.__name__, description=_TOOLS[function.__name__][1])
    for tool in server._tool_manager.list_tools():
        # The local schema forbids unknown arguments (`extra="forbid"`); FastMCP's argument model ignores them. Forbid
        # them there too (FastMCP validates calls with that model, not with the advertised schema) and say so in the
        # advertised schema.
        arguments = tool.fn_metadata.arg_model
        arguments.model_config["extra"] = "forbid"
        arguments.model_rebuild(force=True)
        tool.parameters["additionalProperties"] = False

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
    env["FACT_ATTACK_SKIP_DOTENV"] = "1"  # src.config then loads no .env file in the child
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
