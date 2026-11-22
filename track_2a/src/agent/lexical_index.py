"""Lexical (BM25) index over every vote of the local booklets: the agent's source search without the dense stack.

The same interface as `SourceIndex` (`votes`, `skipped`, `metadata`, `search_votes`, `search_passages`, `close`), so
the agent's tools and guard run on it unchanged. It needs no sentence-transformers, no Qdrant and no model download:
the booklets are parsed with the engine's cached layout and held in memory.

Scores are BM25 divided by the best score of the query over the whole corpus, so the best chunk scores 1.0 and the
guard's margin (calibrated on cosine scores in [0, 1]) still means "nearly tied with rank 1".
"""
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agent import settings
from src.agent.guard import Thresholds
from src.agent.source_index import SNIPPET_CHARS, TOP_CHUNKS, VoteCandidate, VoteRef, _booklet_votes
from src.booklet_catalog import available_sources
from src.text_utils import split_passages

# The guard's thresholds for this index (the settings' values are calibrated on cosine scores, where a gap of 0.01 is
# a near tie; on these scores the same vote in two booklets, e.g. the E-ID law of 2021 and 2025, lies 0.04 apart).
# A vote is "close" within 0.25 of rank 1 and with at least 40 % of its hits: then the agent asks which one is meant.
# A stray vote with a few chunk hits (CO2 law for an E-ID claim: 3 of 11 hits) is not offered.
GUARD_THRESHOLDS = Thresholds(score_margin=0.25, min_hits_ratio=0.4, none_below=None)
STEM_CHARS = 6  # a cheap stemmer: "Zivildienstgesetzes" and "Zivildienstgesetz" share "zivild"
_STOPWORDS = frozenset("""
aber alle allem allen aller alles als also am an ander andere anderen auch auf aus bei beim bin bis bist da damit
dann das dass dazu dem den denn der des deshalb die dies diese diesem diesen dieser dieses doch dort du durch ein
eine einem einen einer eines er es etwa euch für gegen hat hatte hätte haben hier hin ich ihr ihre im in ist ja jede
jedem jeden jeder jedes kann kein keine können man mehr mit muss nach nicht noch nun nur ob oder ohne sehr sein seine
sich sie sind so soll sollen sondern sowie über um und uns unter vom von vor war waren wenn werden wie wieder will
wir wird wurde würde zu zum zur zwar zwischen
""".split())


def tokens(text: str) -> List[str]:
    plain = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    found = []
    for word in re.findall(r"\w+", plain):
        if word.isdigit():
            found.append(word)
        elif len(word) >= 3 and word not in _STOPWORDS:
            found.append(word[:STEM_CHARS])
    return found


class LexicalSourceIndex:
    """Votes of the available booklets in `sources` (the output of `load_catalog`) in the requested languages."""

    guard_thresholds = GUARD_THRESHOLDS

    def __init__(self, sources: Dict[str, dict], booklets_dir: Path, *,
                 languages: Tuple[str, ...] = settings.LANGUAGES,
                 layout: Optional[Callable[[Path], Tuple[list, Optional[list]]]] = None):
        languages = tuple(languages)
        wanted = {name: entry for name, entry in sorted(sources.items()) if entry["language"] in languages}
        available = available_sources(booklets_dir, wanted)
        self.skipped: List[str] = [name for name in wanted if name not in available]
        self.votes: Dict[str, VoteRef] = {}
        self._records: List[dict] = []
        engine = None
        if layout is None:  # the engine's parse cache makes every later start cheap
            from src.apertus_client import ApertusClient
            from src.inference import ClaimVerificationEngine
            engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))
            layout = engine.booklet_layout
        try:
            for name, entry in available.items():
                source_id = Path(name).stem
                paragraphs, parts = layout(Path(booklets_dir) / name)
                refs = _booklet_votes(source_id, entry, paragraphs, parts)
                self.votes.update((ref.vote_id, ref) for ref in refs)
                # The title is a record of its own (not part of every chunk, which would erase its weight):
                # a claim that names the vote finds it. It is never returned as a passage.
                for ref in refs:
                    self._records.append({"vote_id": ref.vote_id, "source_id": source_id,
                                          "page_number": ref.pages[0] if ref.pages else 1, "section": None,
                                          "text": ref.title, "title": True})
                owners: Dict[int, List[VoteRef]] = {}
                for ref in refs:
                    for page in ref.pages:
                        owners.setdefault(page, []).append(ref)
                for paragraph in paragraphs:
                    page = int(paragraph["page_number"])
                    for text in split_passages(paragraph["text"]):
                        for ref in owners.get(page, []):
                            self._records.append({"vote_id": ref.vote_id, "source_id": source_id,
                                                  "page_number": page, "section": paragraph.get("section"),
                                                  "text": text})
        finally:
            if engine is not None:
                engine.close()
        self._index([tokens(r["text"]) for r in self._records])
        self.metadata: Dict[str, Any] = {"kind": "lexical", "languages": list(languages), "sources": len(available),
                                         "votes": len(self.votes), "chunks": len(self._records)}

    def _index(self, corpus: List[List[str]]) -> None:
        """BM25 with the Lucene IDF, which stays positive for words in more than half of the chunks."""
        self._counts = [Counter(words) for words in corpus]
        self._lengths = [len(words) for words in corpus]
        self._average = (sum(self._lengths) / len(corpus)) if corpus else 0.0
        frequency = Counter(word for counts in self._counts for word in counts)
        self._idf = {word: math.log(1 + (len(corpus) - n + 0.5) / (n + 0.5)) for word, n in frequency.items()}

    def _scores(self, words: List[str], k1: float = 1.5, b: float = 0.75) -> List[float]:
        query = [w for w in dict.fromkeys(words) if w in self._idf]
        scores = []
        for counts, length in zip(self._counts, self._lengths):
            norm = k1 * (1 - b + b * length / self._average) if self._average else k1
            scores.append(sum(self._idf[w] * counts[w] * (k1 + 1) / (counts[w] + norm) for w in query if w in counts))
        return scores

    def _search(self, query: str, limit: int, vote_id: Optional[str] = None,
                titles: bool = True) -> List[Tuple[float, int]]:
        """(normalized score, record index), best first; ties: first indexed first. Records without a match drop."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be non-empty text")
        if limit <= 0:
            raise ValueError("k must be positive")
        words = tokens(query)
        if not self._records or not words:
            return []
        scores = self._scores(words)
        top = float(max(scores)) if len(scores) else 0.0
        if top <= 0:
            return []
        hits = [(float(score) / top, i) for i, score in enumerate(scores)
                if score > 0 and (vote_id is None or self._records[i]["vote_id"] == vote_id)
                and (titles or not self._records[i].get("title"))]
        hits.sort(key=lambda hit: (-hit[0], hit[1]))
        return hits[:limit]

    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]:
        """The top-50 chunks of the query, aggregated per vote; hits = chunk count.

        score = 0.4 * the vote's share of the summed scores of all top chunks (its text matches the claim throughout)
              + 0.4 * the share of the claim's words in the vote's title
              + 0.2 * its best chunk,
        so one rare word in a single chunk of another vote does not outweigh the vote the claim is about. The best
        vote is scaled to 1.0, like the best chunk."""
        if k <= 0:
            raise ValueError("k must be positive")
        best: Dict[str, dict] = {}
        hits = self._search(query, TOP_CHUNKS)
        total = sum(score for score, _ in hits) or 1.0
        for score, i in hits:
            record = self._records[i]
            found = best.setdefault(record["vote_id"], {"best": score, "mass": 0.0, "hits": 0, "record": None})
            found["hits"] += 1
            found["mass"] += score
            if found["record"] is None and not record.get("title"):
                found["record"] = record  # page and snippet come from the best text chunk, not from the title
        words = set(tokens(query))
        for vote_id, found in best.items():
            title = set(tokens(self.votes[vote_id].title))
            found["score"] = (0.4 * found["mass"] / total + 0.4 * len(words & title) / max(1, len(words))
                              + 0.2 * found["best"])
        top = max((found["score"] for found in best.values()), default=1.0) or 1.0
        for found in best.values():
            found["score"] /= top
        ranked = sorted(best.items(), key=lambda item: (-item[1]["score"], -item[1]["hits"], item[0]))
        candidates = []
        for vote_id, found in ranked[:k]:
            record = found["record"] or next(r for r in self._records if r["vote_id"] == vote_id)
            candidates.append(VoteCandidate(vote=self.votes[vote_id], score=found["score"], hits=found["hits"],
                                            best_page=int(record["page_number"]),
                                            snippet=" ".join(record["text"].split())[:SNIPPET_CHARS]))
        return candidates

    def search_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]:
        """The `k` chunks of one vote that best match the query, original text; ValueError for an unknown vote_id."""
        if not isinstance(vote_id, str) or vote_id not in self.votes:
            raise ValueError(f"Unknown vote_id: {vote_id!r}")
        hits = self._search(query, k, vote_id=vote_id, titles=False)
        if len(hits) < k:  # like the dense search, always up to k chunks: the rest in booklet order, score 0
            seen = {i for _, i in hits}
            hits += [(0.0, i) for i, r in enumerate(self._records)
                     if r["vote_id"] == vote_id and not r.get("title") and i not in seen][:k - len(hits)]
        return [{**{key: self._records[i][key] for key in ("page_number", "text", "section", "vote_id")},
                 "retrieval_score": score}
                for score, i in hits]

    def close(self) -> None:
        self._records = []
