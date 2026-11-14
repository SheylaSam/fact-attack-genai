"""The agent's three search tools apart from their transport.

`AgentTools` (tools.py) validates arguments, keeps the passage pool and writes the German observations; what it
searches with is a `SearchBackend`. `LocalBackend` runs the index and the engine in this process. Later transports
(an MCP server) implement the same protocol.
"""
import copy
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Protocol

from src.agent.source_index import SourceIndex, VoteCandidate, VoteRef
from src.agent.tools import TERMINAL_TOOLS, TOOL_SPECS, engine_vote
from src.agent.uploads import is_upload_source

_CATALOG_SOURCE = re.compile(r"\d{4}-\d{2}-\d{2}_(de|fr|it)")  # like the file names `load_catalog` accepts


class SearchBackend(Protocol):
    votes: Dict[str, VoteRef]

    def tool_specs(self) -> List[dict]:
        """The model-facing specs of the three search tools, in `TOOL_SPECS` order."""

    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]: ...

    def proven_passages(self, vote_id: str, claim: str) -> List[Dict[str, Any]]:
        """The proven search with the claim over the vote's booklet: unfiltered, in engine order."""

    def vector_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]: ...

    def close(self) -> None: ...


class LocalBackend:
    """Index and engine in this process. `close` is a no-op: the CLI owns both and closes them.

    `add_vote` registers a session-local whole-booklet vote for this backend (one per check): an own PDF upload, or
    a catalog booklet the index has no "#0" for (also a French or Italian booklet: the index is German only). The
    index is shared by every session of the app, so the vote is kept here and never added to `index.votes`.
    `catalog` is the allowlist of catalog file names ("2024-11-24_fr.pdf", the keys of `load_catalog`); without it
    only the index makes a catalog source known."""

    def __init__(self, index: SourceIndex, engine, booklets_dir: Path, *, catalog: Iterable[str] = ()):
        self.index = index
        self.engine = engine
        self.booklets_dir = Path(booklets_dir)
        self._catalog = frozenset(catalog)
        self._session_votes: Dict[str, VoteRef] = {}

    @property
    def votes(self) -> Dict[str, VoteRef]:
        return {**self.index.votes, **self._session_votes} if self._session_votes else self.index.votes

    def _in_catalog(self, source_id: str) -> bool:
        """The source is a booklet of the catalog allowlist, named like one: never a path."""
        return _CATALOG_SOURCE.fullmatch(source_id) is not None and f"{source_id}.pdf" in self._catalog

    def add_vote(self, vote: VoteRef) -> None:
        """Makes a whole-booklet vote ("<source_id>#0" with pages) known to this backend, for an upload
        ("custom_<hash>"), a source the index knows or a booklet of the catalog allowlist. ValueError for anything
        else: a part of a booklet, a vote without pages, an id that is not exactly the source's own, a source that is
        none of these (no free path: `booklets_dir / f"{source_id}.pdf"` must stay inside the directory) and an id
        the index already has (a catalog vote is never shadowed)."""
        known = (is_upload_source(vote.source_id) or self._in_catalog(vote.source_id)
                 or any(ref.source_id == vote.source_id for ref in self.index.votes.values()))
        if (not known or vote.vote_id != f"{vote.source_id}#0" or not vote.pages
                or vote.vote_id in self.index.votes):
            raise ValueError(f"Nur ein ganzes Büchlein kann als Vorlage ergänzt werden, nicht {vote.vote_id!r}.")
        self._session_votes[vote.vote_id] = vote

    def tool_specs(self) -> List[dict]:
        return [copy.deepcopy(spec) for spec in TOOL_SPECS if spec["name"] not in TERMINAL_TOOLS]

    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]:
        return self.index.search_votes(query, k=k)

    def proven_passages(self, vote_id: str, claim: str) -> List[Dict[str, Any]]:
        vote = self.votes[vote_id]
        return self.engine.retrieve_passages(
            claim=claim, booklet_pdf=self.booklets_dir / f"{vote.source_id}.pdf", vote=engine_vote(vote),
            booklet_language=vote.language)

    def vector_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]:
        if vote_id in self._session_votes:  # not in the index: the Nachsuche ends without new passages
            return []
        return self.index.search_passages(vote_id, query, k=k)

    def close(self) -> None:
        pass
