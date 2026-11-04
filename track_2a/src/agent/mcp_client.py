"""`McpBackend`: the agent's `SearchBackend` over the own MCP server (mcp_server.py), spoken over stdio.

The graph is synchronous, the MCP client is asyncio. The backend therefore runs its own event loop in a background
thread. One owner coroutine on that loop enters `stdio_client` and `ClientSession`, loads the tools with
`langchain-mcp-adapters`, reads the vote catalogue and then waits for the close signal; it leaves the contexts in the
same task (anyio requires that). A call from the graph thread is `run_coroutine_threadsafe(tool.ainvoke(...))` with a
timeout.

A dead, stuck or failing server is always a visible failure and never a result: every call raises `RuntimeError`
(German message) within `call_timeout`, `AgentTools.run` turns that into a tool failure and the graph ends as
`technischer_fehler`. There is no fallback to local tools; the server environment comes from `server_environment`
(an allow list, so no secret reaches the process).
"""
import asyncio
import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence

import anyio
from src.agent import settings
from src.agent.mcp_server import INJECTED_ARGS, SEARCH_TOOLS, VOTES_URI
from src.agent.source_index import VoteCandidate, VoteRef

from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CLOSE_TIMEOUT_S = 10.0  # `close` waits at most this long for the server to end
STDERR_LINES = 20  # lines of the server's stderr that a start error carries


class McpStartError(RuntimeError):
    """The server did not start or did not become ready. The message is German and ends with the last lines of the
    server's stderr."""


class _StartTimeout(Exception):
    """The handshake did not finish within `start_timeout`."""


def _drop_titles(node: Any) -> Any:
    """`node` without the "title" annotations of a JSON schema. A property that is itself called "title" stays."""
    if isinstance(node, list):
        return [_drop_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: _drop_titles(prop) for name, prop in value.items()}
        else:
            cleaned[key] = _drop_titles(value)
    return cleaned


def model_spec(tool: BaseTool) -> dict:
    """The model-facing spec of an MCP tool: name, description and the parameters without what the graph injects
    (`INJECTED_ARGS`, also from `required`) and without "title" keys."""
    schema = tool.args_schema
    if not isinstance(schema, dict):
        schema = schema.model_json_schema() if schema is not None else {"type": "object", "properties": {}}
    parameters = _drop_titles(copy.deepcopy(schema))
    hidden = INJECTED_ARGS.get(tool.name, ())
    parameters["properties"] = {name: prop for name, prop in parameters.get("properties", {}).items()
                                if name not in hidden}
    if "required" in parameters:
        parameters["required"] = [name for name in parameters["required"] if name not in hidden]
    return {"name": tool.name, "description": tool.description, "parameters": parameters}


def _describe(exc: BaseException) -> str:
    """The innermost message of an exception, also of the exception groups anyio raises."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc).strip()
    if isinstance(exc, _StartTimeout):
        return text
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _decode(result: Any, tool: str) -> dict:
    """A tool result (JSON text or a list of content blocks with text) as a dict."""
    if isinstance(result, str):
        text = result
    elif isinstance(result, list):
        text = "".join(block if isinstance(block, str) else str(block.get("text", ""))
                       for block in result if isinstance(block, (str, dict)))
    else:
        raise RuntimeError(f"Unerwartete Antwort des MCP-Werkzeugs {tool}: {type(result).__name__}.")
    try:
        data = json.loads(text)
    except ValueError:
        raise RuntimeError(f"Die Antwort des MCP-Werkzeugs {tool} ist kein JSON: {text[:200]!r}") from None
    if not isinstance(data, dict):
        raise RuntimeError(f"Die Antwort des MCP-Werkzeugs {tool} hat nicht die erwartete Form.")
    return data


class McpBackend:
    """A `SearchBackend` over the own MCP server. `start()` before use; `close()` ends the server."""

    def __init__(self, command: Sequence[str], *, env: Mapping[str, str], cwd: Path,
                 start_timeout: float = settings.MCP_START_TIMEOUT_S,
                 call_timeout: float = settings.MCP_CALL_TIMEOUT_S):
        if not command:
            raise ValueError("Der Befehl des MCP-Servers fehlt.")
        self.command = list(command)
        self.env = dict(env)
        self.cwd = Path(cwd)
        self.start_timeout = start_timeout
        self.call_timeout = call_timeout
        self.votes: Dict[str, VoteRef] = {}
        self._tools: Dict[str, BaseTool] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._owner: Optional[concurrent.futures.Future] = None
        self._closing: Optional[asyncio.Event] = None
        self._errlog = None
        self._ready = False
        self._closed = False
        self._lock = threading.Lock()

    # --- lifecycle -----------------------------------------------------------------------------------------

    def start(self) -> None:
        """Starts the server and waits until its tools and votes are loaded. `McpStartError` on any failure; the
        server process is ended then."""
        with self._lock:
            if self._loop is not None or self._closed:
                raise McpStartError("Der MCP-Server wurde schon gestartet oder beendet.")
            self._errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-backend", daemon=True)
            self._thread.start()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        self._owner = asyncio.run_coroutine_threadsafe(self._own(ready), self._loop)
        try:
            # the owner enforces start_timeout itself (see `_own`); this limit only backs it up
            ready.result(self.start_timeout + CLOSE_TIMEOUT_S)
            self._ready = True
        except concurrent.futures.TimeoutError:
            self._abort(McpStartError(self._start_message(f"Zeitüberschreitung nach {self.start_timeout:g} s")))
        except BaseException as exc:
            self._abort(McpStartError(self._start_message(_describe(exc))))

    def _start_message(self, reason: str) -> str:
        return (f"Der MCP-Server wurde nicht bereit ({reason}). Befehl: {' '.join(self.command[:3])} …\n"
                f"Letzte Zeilen der Fehlerausgabe des Servers:\n{self._stderr_tail()}")

    def _stderr_tail(self) -> str:
        try:
            self._errlog.flush()
            self._errlog.seek(0)
            lines = self._errlog.read().splitlines()
        except Exception:
            return "(nicht lesbar)"
        return "\n".join(lines[-STDERR_LINES:]) or "(leer)"

    def _abort(self, error: McpStartError) -> None:
        self.close()
        raise error

    async def _own(self, ready: "concurrent.futures.Future") -> None:
        """Owns the server connection: opens it, reports ready, waits for the close signal, closes it again in
        this same task."""
        self._closing = asyncio.Event()
        params = StdioServerParameters(command=self.command[0], args=self.command[1:], env=self.env,
                                       cwd=str(self.cwd))
        try:
            async with stdio_client(params, errlog=self._errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    # The limit is an anyio scope inside the contexts: running out leaves them through the normal
                    # exit path, which ends the server process. Cancelling the whole task would skip that cleanup.
                    with anyio.move_on_after(self.start_timeout) as handshake:
                        await session.initialize()
                        tools = await load_mcp_tools(session, handle_tool_errors=False)
                        self._tools = self._checked_tools(tools)
                        self.votes = await self._read_votes(session)
                    if handshake.cancelled_caught:
                        raise _StartTimeout(f"Zeitüberschreitung nach {self.start_timeout:g} s")
                    if not ready.done():
                        ready.set_result(None)
                    await self._closing.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise

    @staticmethod
    def _checked_tools(tools: List[BaseTool]) -> Dict[str, BaseTool]:
        """The server must offer exactly the search tools: a missing one would silently vanish from the model's
        options (AgentTools.available_specs), an extra one would be an unknown tool."""
        by_name = {tool.name: tool for tool in tools}
        if set(by_name) != set(SEARCH_TOOLS) or len(by_name) != len(tools):
            raise RuntimeError(f"Der MCP-Server bietet {sorted(by_name)} statt {sorted(SEARCH_TOOLS)}; "
                               f"es fehlt: {sorted(set(SEARCH_TOOLS) - set(by_name)) or 'nichts'}.")
        return {name: by_name[name] for name in SEARCH_TOOLS}

    @staticmethod
    async def _read_votes(session: ClientSession) -> Dict[str, VoteRef]:
        resource = await session.read_resource(VOTES_URI)
        votes = {}
        for item in json.loads(resource.contents[0].text)["votes"]:
            vote = VoteRef(**{**item, "pages": tuple(item["pages"])})
            votes[vote.vote_id] = vote
        return votes

    def close(self) -> None:
        """Ends the server and the background thread. Idempotent; waits at most `CLOSE_TIMEOUT_S`."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread, owner, closing = self._loop, self._thread, self._owner, self._closing
            graceful = self._ready
            self._ready = False
        try:
            if loop is not None and owner is not None:
                if graceful and closing is not None:  # a running server: the owner leaves its contexts on the signal
                    loop.call_soon_threadsafe(closing.set)
                else:  # a start that did not finish: the owner is blocked in the handshake
                    owner.cancel()
                try:
                    owner.result(CLOSE_TIMEOUT_S)
                except BaseException:  # not closed in time, or cancelled, or the owner had failed: cancel and wait
                    owner.cancel()
                    try:
                        owner.result(CLOSE_TIMEOUT_S)
                    except BaseException:
                        pass
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(CLOSE_TIMEOUT_S)
            if loop is not None and (thread is None or not thread.is_alive()) and not loop.is_running():
                loop.close()  # only once the loop has stopped and its thread has ended
        finally:
            errlog, self._errlog = self._errlog, None
            if errlog is not None:
                try:
                    errlog.close()
                except Exception:
                    pass

    # --- SearchBackend -----------------------------------------------------------------------------------------

    def tool_specs(self) -> List[dict]:
        return [model_spec(tool) for tool in self._tools.values()]

    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]:
        data = self._call("suche_vorlagen", {"anfrage": query})
        try:
            return [VoteCandidate(vote=self.votes[item["vote_id"]], score=item["score"], hits=item["hits"],
                                  best_page=item["best_page"], snippet=item["snippet"])
                    for item in data["candidates"]][:k]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"Die Antwort von suche_vorlagen passt nicht zum Vorlagenkatalog: "
                               f"{_describe(exc)}") from None

    def proven_passages(self, vote_id: str, claim: str) -> List[Dict[str, Any]]:
        # the claim is injected here: it is not a parameter the model sees (INJECTED_ARGS)
        return self._passages(self._call("suche_passagen", {"vorlage_id": vote_id, "behauptung": claim}),
                              "suche_passagen")

    def vector_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]:
        data = self._call("suche_passagen_vektor", {"vorlage_id": vote_id, "anfrage": query})
        return self._passages(data, "suche_passagen_vektor")[:k]

    @staticmethod
    def _passages(data: dict, tool: str) -> List[Dict[str, Any]]:
        passages = data.get("passages")
        if not isinstance(passages, list):
            raise RuntimeError(f"Die Antwort von {tool} enthält keine Passagenliste.")
        return passages

    def _call(self, name: str, args: Dict[str, Any]) -> dict:
        if not self._ready or self._loop is None:
            raise RuntimeError("Der MCP-Server ist nicht gestartet oder bereits beendet.")
        future = asyncio.run_coroutine_threadsafe(self._tools[name].ainvoke(args), self._loop)
        try:
            result = future.result(self.call_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError(f"Der MCP-Server hat das Werkzeug {name} nicht innerhalb von "
                               f"{self.call_timeout:g} s beantwortet.") from None
        except ToolException as exc:  # isError of the server: its German message stays in ours
            raise RuntimeError(f"Der MCP-Server meldet einen Fehler bei {name}: {exc}") from None
        except Exception as exc:  # a dead process, a closed stream
            raise RuntimeError(f"Der MCP-Server ist nicht erreichbar (Werkzeug {name}): {_describe(exc)}") from None
        return _decode(result, name)
