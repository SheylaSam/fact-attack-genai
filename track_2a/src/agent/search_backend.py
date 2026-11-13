"""The agent's three search tools apart from their transport.

`AgentTools` (tools.py) validates arguments, keeps the passage pool and writes the German observations; what it
searches with is a `SearchBackend`. `LocalBackend` runs the index and the engine in this process. Later transports
(an MCP server) implement the same protocol.
"""
import copy
from pathlib import Path
from typing import Any, Dict, List, Protocol

from src.agent.source_index import SourceIndex, VoteCandidate, VoteRef
from src.agent.tools import TERMINAL_TOOLS, TOOL_SPECS, engine_vote
from src.agent.uploads import is_upload_source


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

    `add_vote` registers an own PDF upload for this backend (one per check). The index is shared by every session
    of the app, so the upload is kept here and never added to `index.votes`."""

    def __init__(self, index: SourceIndex, engine, booklets_dir: Path):
        self.index = index
        self.engine = engine
        self.booklets_dir = Path(booklets_dir)
        self._uploads: Dict[str, VoteRef] = {}

    @property
    def votes(self) -> Dict[str, VoteRef]:
        return {**self.index.votes, **self._uploads} if self._uploads else self.index.votes

    def add_vote(self, vote: VoteRef) -> None:
        """Makes an uploaded booklet's whole-booklet vote ("custom_<hash>#0") known to this backend. ValueError for
        anything else: a catalog vote, a part of a booklet, a vote without pages or an id that is not exactly the
        upload's own (`booklets_dir / f"{source_id}.pdf"` must stay inside the directory)."""
        if (not is_upload_source(vote.source_id) or vote.vote_id != f"{vote.source_id}#0" or not vote.pages):
            raise ValueError(f"Nur eigene PDFs können als Vorlage ergänzt werden, nicht {vote.vote_id!r}.")
        self._uploads[vote.vote_id] = vote

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
        if vote_id in self._uploads:  # the index does not know uploads: the Nachsuche ends without new passages
            return []
        return self.index.search_passages(vote_id, query, k=k)

    def close(self) -> None:
        pass
