"""Dense index over every vote of the catalog's booklets: finds the vote ("Vorlage") a free-form claim is about.

One Qdrant point per chunk and vote. A chunk belongs to the vote(s) whose pages contain its page; pages that belong to
no vote (cover, contents) are not indexed. The vote ranking and the passage search of the agent's tools run on it.
Booklet parsing (`layout`) is only needed to build an index; a persisted index is opened without parsing anything.
"""
import dataclasses
from dataclasses import dataclass
import hashlib
import json
import os
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src import part_context
from src.agent import settings
from src.booklet_catalog import available_sources
from src.vector_rag import E5Encoder, VectorRetriever

SCHEMA_VERSION = 1
WHOLE_BOOKLET_TITLE = "Gesamtes Büchlein"
TOP_CHUNKS = 50  # chunk hits that search_votes aggregates per vote
SNIPPET_CHARS = 200
UPSERT_BATCH = 128
COLLECTION = "votes"
MANIFEST = "votes.json"  # written last: its presence marks a complete index


@dataclass(frozen=True)
class VoteRef:
    vote_id: str  # f"{source_id}#{k}", k 1-based in the order of the booklet's parts; "#0" = whole booklet
    source_id: str  # "2024-11-24_de"
    date: str
    language: str
    title: str
    pages: Tuple[int, ...]


@dataclass(frozen=True)
class VoteCandidate:
    vote: VoteRef
    score: float  # best chunk score (cosine)
    hits: int  # chunks of the vote among the top hits
    best_page: int
    snippet: str  # start of the best chunk, at most SNIPPET_CHARS characters


def _booklet_votes(source_id: str, entry: dict, paragraphs: list, parts: Optional[list]) -> List[VoteRef]:
    """The votes of one parsed booklet; the shared `paragraphs` and `parts` objects are only read."""
    date, language = entry["date"], entry["language"]
    if not parts:  # no readable contents page: the booklet is one vote
        pages = tuple(sorted({int(p["page_number"]) for p in paragraphs}))
        return [VoteRef(f"{source_id}#0", source_id, date, language, WHOLE_BOOKLET_TITLE, pages)]
    catalog_titles: Dict[int, str] = {}  # id(part) -> the first catalog title match_vote assigns to it
    for title in entry.get("votes", []):
        matched = part_context.match_vote(parts, title)
        if matched is not None:
            catalog_titles.setdefault(id(matched), title)
    refs = []
    for k, vote in enumerate(parts, 1):
        pages = tuple(sorted({int(page) for pages in vote["parts"].values() for page in pages if page >= 1}))
        refs.append(VoteRef(f"{source_id}#{k}", source_id, date, language,
                            catalog_titles.get(id(vote), vote["title"]), pages))
    return refs


def _snippet(text: str) -> str:
    return " ".join(text.split())[:SNIPPET_CHARS]


class SourceIndex:
    """Votes of all booklets in `sources` (the output of `load_catalog`) in the requested languages.

    `votes`: vote_id -> VoteRef; `skipped`: catalog PDFs that are missing or not PDFs (never parsed, never indexed).
    A persistent index lives in `index_dir/<fingerprint>`; the fingerprint covers the schema, the Qdrant client, the
    encoder, the languages and every indexed source (name, SHA-256 and vote titles from the catalog).
    """

    def __init__(self, sources: Dict[str, dict], booklets_dir: Path, *, encoder=None, index_dir: Optional[Path] = None,
                 languages: Tuple[str, ...] = settings.LANGUAGES,
                 layout: Optional[Callable[[Path], Tuple[list, Optional[list]]]] = None):
        from qdrant_client import QdrantClient, models
        self._models = models
        self._closed = False
        self._has_chunks = False
        self.encoder = encoder or E5Encoder()
        self.votes: Dict[str, VoteRef] = {}
        languages = tuple(languages)
        wanted = {name: entry for name, entry in sorted(sources.items()) if entry["language"] in languages}
        available = available_sources(booklets_dir, wanted)  # exists and starts with %PDF-, before any parsing
        self.skipped: List[str] = [name for name in wanted if name not in available]
        self.fingerprint = self._fingerprint(available, languages)
        self.metadata: Dict[str, Any] = {
            "schema": SCHEMA_VERSION, "index_fingerprint": self.fingerprint, "languages": list(languages),
            "sources": len(available), "encoder": dict(self.encoder.identity)}
        self._directory = None
        if index_dir:
            self._directory = Path(index_dir) / self.fingerprint
            self._directory.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(self._directory), force_disable_check_same_thread=True)
        else:
            self.client = QdrantClient(":memory:")
        try:
            manifest = self._read_manifest()
            if manifest is not None:
                self._reuse(manifest)
            else:
                self._build(available, Path(booklets_dir), layout)
            self.metadata["votes"] = len(self.votes)
        except BaseException:
            self.client.close()
            raise

    def _fingerprint(self, available: Dict[str, dict], languages: Tuple[str, ...]) -> str:
        indexed = []
        for name, entry in available.items():
            if not entry.get("sha256"):
                raise ValueError(f"Catalog entry {name} has no sha256: the index cannot be bound to its source")
            indexed.append([name, entry["sha256"], list(entry.get("votes", []))])
        signature = {"schema": SCHEMA_VERSION, "qdrant_client": version("qdrant-client"),
                     "encoder": self.encoder.identity, "languages": sorted(languages), "sources": sorted(indexed)}
        return hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False,
                                         default=str).encode()).hexdigest()

    def _read_manifest(self) -> Optional[dict]:
        if self._directory is None or not (self._directory / MANIFEST).is_file():
            return None
        return json.loads((self._directory / MANIFEST).read_text(encoding="utf-8"))

    def _write_manifest(self, chunks: int) -> None:
        if self._directory is None:
            return
        manifest = {"schema": SCHEMA_VERSION, "chunks": chunks,
                    "votes": [dataclasses.asdict(ref) for ref in self.votes.values()]}
        temporary = self._directory / (MANIFEST + ".tmp")
        temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self._directory / MANIFEST)

    def _reuse(self, manifest: dict) -> None:
        self.votes = {v["vote_id"]: VoteRef(**{**v, "pages": tuple(v["pages"])}) for v in manifest["votes"]}
        chunks = manifest["chunks"]
        if chunks and (not self.client.collection_exists(COLLECTION)
                       or self.client.count(COLLECTION, exact=True).count != chunks):
            raise ValueError("Incomplete index: remove its fingerprint directory and rebuild")
        self._has_chunks = chunks > 0
        self.metadata.update(index_reused=True, chunks=chunks)

    def _build(self, available: Dict[str, dict], booklets_dir: Path, layout) -> None:
        if self.client.collection_exists(COLLECTION):  # a build that stopped before its manifest was written
            self.client.delete_collection(COLLECTION)
        engine = None
        if layout is None:  # the engine's parse cache makes every later build cheap
            from src.apertus_client import ApertusClient
            from src.inference import ClaimVerificationEngine
            engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
            layout = engine.booklet_layout
        try:
            records = []
            for name, entry in available.items():
                source_id = Path(name).stem
                paragraphs, parts = layout(booklets_dir / name)
                refs = _booklet_votes(source_id, entry, paragraphs, parts)
                self.votes.update((ref.vote_id, ref) for ref in refs)
                owners: Dict[int, List[VoteRef]] = {}
                for ref in refs:
                    for page in ref.pages:
                        owners.setdefault(page, []).append(ref)
                for paragraph in paragraphs:
                    for ref in owners.get(int(paragraph["page_number"]), []):
                        for text in self.encoder.chunks(paragraph["text"]):
                            if text.strip():
                                records.append({"vote_id": ref.vote_id, "source_id": source_id,
                                                "page_number": int(paragraph["page_number"]),
                                                "section": paragraph.get("section"), "text": text})
        finally:
            if engine is not None:
                engine.close()
        self._index(records)
        self._write_manifest(len(records))
        self.metadata.update(index_reused=False, chunks=len(records))

    def _index(self, records: List[dict]) -> None:
        """Embed every distinct chunk text once and store one point per (chunk, vote)."""
        by_text: Dict[str, List[dict]] = {}
        for record in records:
            by_text.setdefault(record["text"], []).append(record)
        texts = list(by_text)
        size, point_id = None, 0
        for start in range(0, len(texts), UPSERT_BATCH):
            batch = texts[start:start + UPSERT_BATCH]
            vectors = self.encoder.encode(batch)
            VectorRetriever._validate_vectors(vectors, len(batch))
            if size is None:
                size = len(vectors[0])
                self.client.create_collection(COLLECTION, vectors_config=self._models.VectorParams(
                    size=size, distance=self._models.Distance.COSINE))
            elif len(vectors[0]) != size:
                raise ValueError("Embeddings must have equal dimensions")
            points = []
            for text, vector in zip(batch, vectors):
                for record in by_text[text]:
                    points.append(self._models.PointStruct(id=point_id, vector=vector, payload=record))
                    point_id += 1
            self.client.upsert(COLLECTION, points=points, wait=True)
        self._has_chunks = point_id > 0

    def _search(self, query: str, limit: int, vote_id: Optional[str] = None):
        """Hits best first (ties: first indexed first); the cosine score is `hit.score`."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be non-empty text")
        if limit <= 0:
            raise ValueError("k must be positive")
        if not self._has_chunks:
            return []
        vectors = self.encoder.encode([query], query=True)
        VectorRetriever._validate_vectors(vectors, 1)
        models = self._models
        only_vote = None if vote_id is None else models.Filter(must=[
            models.FieldCondition(key="vote_id", match=models.MatchValue(value=vote_id))])
        hits = self.client.query_points(COLLECTION, query=vectors[0], query_filter=only_vote, limit=limit,
                                        with_payload=True).points
        return sorted(hits, key=lambda hit: (-float(hit.score), int(hit.id)))

    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]:
        """The top-50 chunks of the query, aggregated per vote: score = best chunk, hits = chunk count."""
        if k <= 0:
            raise ValueError("k must be positive")
        best: Dict[str, dict] = {}
        for hit in self._search(query, TOP_CHUNKS):
            vote_id = hit.payload["vote_id"]
            if vote_id in best:
                best[vote_id]["hits"] += 1
            else:  # hits come best first: the first chunk of a vote is its best
                best[vote_id] = {"score": float(hit.score), "hits": 1, "payload": hit.payload}
        ranked = sorted(best.items(), key=lambda item: (-item[1]["score"], -item[1]["hits"], item[0]))
        return [VoteCandidate(vote=self.votes[vote_id], score=found["score"], hits=found["hits"],
                              best_page=int(found["payload"]["page_number"]),
                              snippet=_snippet(found["payload"]["text"]))
                for vote_id, found in ranked[:k]]

    def search_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]:
        """The `k` chunks of one vote closest to the query, original text; ValueError for an unknown vote_id."""
        if not isinstance(vote_id, str) or vote_id not in self.votes:
            raise ValueError(f"Unknown vote_id: {vote_id!r}")
        return [{"page_number": int(hit.payload["page_number"]), "text": hit.payload["text"],
                 "section": hit.payload.get("section"), "vote_id": hit.payload["vote_id"],
                 "retrieval_score": float(hit.score)}
                for hit in self._search(query, k, vote_id=vote_id)]

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.client.close()
