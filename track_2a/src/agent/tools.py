"""Tools of the agent: OpenAI function schemas, validated arguments, German observations and the passage pool.

The LLM only ever sees `Observation.text`. Tool arguments are vote ids of the index and search text of 1-500
characters; nothing else (no paths, no URLs) reaches the index or the engine. The passages every search finds go into
a per-run pool that keeps their original text and deduplicates by (page_number, sha256(text)); only the pool is
checked against the claim. The pool stores `POOL_LIMIT` (twice the check size) passages per vote: the first
`MAX_PASSAGES` of them go into the first check; `recheck_passages` puts the passages the first check has not seen
first.
"""
import copy
import datetime
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from src.agent import guard, settings
from src.agent.source_index import TOP_CHUNKS, VoteCandidate, VoteRef

if TYPE_CHECKING:  # search_backend imports this module
    from src.agent.search_backend import SearchBackend

POOL_LIMIT = 2 * settings.MAX_PASSAGES  # passages the pool stores per vote; `passages_for` hands out MAX_PASSAGES
MAX_QUERY_CHARS = 500
MAX_ID_CHARS = 64
MAX_QUESTION_CHARS = 300
MAX_REASON_CHARS = 500
OPTION_RANGE = (2, 4)
VOTE_CANDIDATES = 5  # votes suche_vorlagen reports
VECTOR_PASSAGES = 8  # passages suche_passagen_vektor asks the index for
SNIPPET_PREVIEW_CHARS = 120  # display only; the pool keeps the whole text
PASSAGE_PREVIEW_CHARS = 200
ERROR_CHARS = 300
OUTSIDE_STATUS = "andere Vorlage, nicht aufgenommen"

ERROR_INVALID_ARGS = "invalid_args"  # `Observation.data["error_kind"]`: the model's mistake, fed back as a search step
ERROR_TOOL_FAILURE = "tool_failure"  # the index or the engine failed: a technical error

TERMINAL_TOOLS = frozenset({"pruefen", "rueckfrage", "quelle_fehlt"})
FIXED_VOTE_HIDDEN_TOOLS = frozenset({"suche_vorlagen", "rueckfrage"})  # nothing to find or to choose between

_Query = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS)]
_VoteId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ID_CHARS)]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _SearchVotesArgs(_Args):
    anfrage: _Query = Field(description="Suchtext, 1 bis 500 Zeichen: Thema der Behauptung in eigenen Worten, "
                                        "dazu genannter Termin oder Vorlagenname.")


class _ProvenSearchArgs(_Args):
    vorlage_id: _VoteId = Field(description="Vorlagen-ID aus der Ergebnisliste von suche_vorlagen, "
                                            "zum Beispiel 2024-11-24_de#1.")


class _VectorSearchArgs(_ProvenSearchArgs):
    anfrage: _Query = Field(description="Suchtext, 1 bis 500 Zeichen: die Aussage oder ihre Stichwörter.")


class _CheckArgs(_Args):
    vorlage_id: _VoteId = Field(description="Vorlagen-ID, deren gesammelte Passagen geprüft werden.")


class _AskArgs(_Args):
    frage: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)] = \
        Field(description="Kurze Rückfrage an die Person, zum Beispiel welche Vorlage gemeint ist.")
    optionen: List[_VoteId] = Field(min_length=OPTION_RANGE[0], max_length=OPTION_RANGE[1],
                                    description="Zwei bis vier verschiedene Vorlagen-IDs als Antwortknöpfe.")

    @field_validator("optionen")
    @classmethod
    def _distinct(cls, value: List[str]) -> List[str]:
        if len(set(value)) != len(value):
            raise ValueError("'optionen' müssen verschiedene Vorlagen-IDs enthalten")
        return value


class _SourceMissingArgs(_Args):
    begruendung: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REASON_CHARS)] = \
        Field(description="Warum keine Vorlage der Büchlein zur Behauptung passt.")


_TOOLS: Dict[str, Tuple[type, str]] = {
    "suche_vorlagen": (_SearchVotesArgs, (
        "Sucht in allen Abstimmungsbüchlein die Vorlagen, die zur Behauptung passen. Liefert bis zu "
        f"{VOTE_CANDIDATES} Kandidaten mit Vorlagen-ID, Abstimmungstermin, Titel, Score und Trefferzahl. "
        "Zuerst aufrufen, solange die Vorlage nicht feststeht.")),
    "suche_passagen": (_ProvenSearchArgs, (
        "Sucht mit dem Wortlaut der Behauptung die Textstellen der gewählten Vorlage (bewährte Suche mit "
        "Stichwörtern und Abschnittslogik, wie im bewährten Prüfpfad). In den Passagenpool kommen nur Stellen von "
        "den Seiten dieser Vorlage. Für dieselbe Vorlage liefert sie immer dieselben Stellen; für eine andere "
        "Formulierung suche_passagen_vektor verwenden.")),
    "suche_passagen_vektor": (_VectorSearchArgs, (
        "Sucht in einer gewählten Vorlage die Textstellen zur Anfrage über die Bedeutung (Vektorsuche). "
        "Sinnvoll, wenn suche_passagen nichts Passendes fand oder die Behauptung anders formuliert ist als der Text. "
        "Die Fundstellen kommen in denselben Passagenpool.")),
    "pruefen": (_CheckArgs, (
        "Beendet die Suche und prüft die Behauptung gegen die gesammelten Passagen der gewählten Vorlage. "
        "Erst aufrufen, wenn für diese Vorlage Passagen gefunden wurden.")),
    "rueckfrage": (_AskArgs, (
        "Beendet den Lauf mit einer Rückfrage an die Person, wenn zwei bis vier Vorlagen gleich gut passen und "
        "die Behauptung nicht erkennen lässt, welche gemeint ist.")),
    "quelle_fehlt": (_SourceMissingArgs, (
        "Beendet den Lauf ohne Urteil, wenn keine Vorlage der Büchlein zur Behauptung passt.")),
}


def _tool_spec(name: str, model: type, description: str) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema["properties"].values():
        prop.pop("title", None)
    return {"name": name, "description": description, "parameters": schema}


TOOL_SPECS: List[dict] = [_tool_spec(name, model, description) for name, (model, description) in _TOOLS.items()]


def _shown(value: Any) -> str:
    """A model-supplied value as it is echoed in messages: short, quoted."""
    text = value if isinstance(value, str) else repr(value)
    return repr(text[:60] + ("…" if len(text) > 60 else ""))


def _describe(error: dict, model: type) -> str:
    loc = error["loc"]
    name = str(loc[0]) + "".join(f"[{part}]" for part in loc[1:]) if loc else "Argumente"
    kind, context = error["type"], error.get("ctx") or {}
    if kind == "missing":
        return f"'{name}' fehlt"
    if kind == "extra_forbidden":
        return f"'{name}' ist kein Argument dieses Werkzeugs (erlaubt: {', '.join(model.model_fields)})"
    if kind == "string_type":
        return f"'{name}' muss Text sein"
    if kind == "string_too_short":
        return (f"'{name}' darf nicht leer sein" if context.get("min_length") == 1
                else f"'{name}' braucht mindestens {context.get('min_length')} Zeichen")
    if kind == "string_too_long":
        length = len(str(error.get("input", "")).strip())
        return f"'{name}' ist zu lang ({length} Zeichen, höchstens {context['max_length']})"
    if kind == "list_type":
        return f"'{name}' muss eine Liste sein"
    if kind == "too_short":
        return f"'{name}' braucht mindestens {context['min_length']} Einträge (angegeben: {context['actual_length']})"
    if kind == "too_long":
        return f"'{name}' erlaubt höchstens {context['max_length']} Einträge (angegeben: {context['actual_length']})"
    if kind == "value_error":
        return str(context.get("error", error["msg"]))
    return f"'{name}': {error['msg']}"


def _unknown_tool(tool: Any) -> str:
    return f"Unbekanntes Werkzeug {_shown(tool)}. Bekannt: {', '.join(_TOOLS)}."


def validate_args(tool: str, args: dict) -> dict:
    """The tool's arguments as validated, stripped plain values; ValueError with a German message otherwise."""
    entry = _TOOLS.get(tool) if isinstance(tool, str) else None
    if entry is None:
        raise ValueError(_unknown_tool(tool))
    if not isinstance(args, dict):
        raise ValueError(f"Die Argumente von {tool} müssen ein Objekt mit den Parametern des Werkzeugs sein.")
    try:
        return entry[0].model_validate(args).model_dump()
    except ValidationError as exc:
        problems = "; ".join(_describe(error, entry[0]) for error in exc.errors())
        raise ValueError(f"Ungültige Argumente für {tool}: {problems}.") from None


@dataclass
class Observation:
    tool: str
    text: str  # compact German text for the LLM
    data: dict = field(default_factory=dict)  # structured result for deciders, trace and UI
    error: Optional[str] = None


def _display_date(date: str) -> str:
    try:
        return datetime.date.fromisoformat(date).strftime("%d.%m.%Y")
    except ValueError:
        return date


def _preview(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def engine_vote(vote: VoteRef) -> Optional[str]:
    """The vote title the engine's retrieval and NLI prompt get; None for the whole-booklet vote ("#0"), whose
    placeholder title names no vote of the booklet."""
    return None if vote.vote_id.endswith("#0") else vote.title


def _passage_key(passage: Dict[str, Any]) -> Tuple[int, str]:
    return int(passage["page_number"]), hashlib.sha256(passage["text"].encode("utf-8")).hexdigest()


class AgentTools:
    """The agent's search tools over one `SearchBackend`; one instance per check (it holds the pool).

    The backend runs the three searches (`suche_passagen` is the proven search with the claim, see `run`). With
    `fixed_vote_id` the vote is given: `suche_vorlagen` and `rueckfrage` are not offered and every other tool
    accepts only that vote.
    """

    def __init__(self, backend: "SearchBackend", booklets_dir: Path, *, fixed_vote_id: Optional[str] = None):
        if fixed_vote_id is not None and fixed_vote_id not in backend.votes:
            raise ValueError(f"Unbekannte feste Vorlage: {_shown(fixed_vote_id)}")
        self.backend = backend
        self.booklets_dir = Path(booklets_dir)
        self.fixed_vote_id = fixed_vote_id
        self._recent_ids: List[str] = []  # the votes of the latest suche_vorlagen
        self._assessments: Dict[str, guard.Assessment] = {}  # claim_assessment cache, keyed by the search text
        self._pool: Dict[str, List[Dict[str, Any]]] = {}
        self._seen: Dict[str, Set[Tuple[int, str]]] = {}
        self._handlers: Dict[str, Callable[[dict, Optional[str]], Observation]] = {
            "suche_vorlagen": self._search_votes,
            "suche_passagen": self._search_passages,
            "suche_passagen_vektor": self._search_passages_by_vector,
        }

    @property
    def votes(self) -> Dict[str, VoteRef]:
        return self.backend.votes

    def available_specs(self) -> List[dict]:
        """The backend's search specs and the module's terminal specs, in `TOOL_SPECS` order."""
        hidden = FIXED_VOTE_HIDDEN_TOOLS if self.fixed_vote_id is not None else frozenset()
        search = {spec["name"]: spec for spec in self.backend.tool_specs()}
        specs = [spec if spec["name"] in TERMINAL_TOOLS else search.get(spec["name"]) for spec in TOOL_SPECS]
        return [copy.deepcopy(spec) for spec in specs if spec is not None and spec["name"] not in hidden]

    def claim_assessment(self, claim: str) -> guard.Assessment:
        """The guard's assessment of the vote ranking for the claim's own wording. The search runs once per
        instance and query; a failure of the backend is passed on and not cached."""
        query = claim[:MAX_QUERY_CHARS].strip()
        if query not in self._assessments:
            self._assessments[query] = guard.assess(self.backend.search_votes(query, k=VOTE_CANDIDATES))
        return self._assessments[query]

    def check_vote_id(self, vote_id: str) -> VoteRef:
        """The vote of this id; ValueError that names the valid ids otherwise."""
        if self.fixed_vote_id is not None:
            if vote_id == self.fixed_vote_id:
                return self.votes[vote_id]
            raise ValueError(f"Unbekannte Vorlagen-ID {_shown(vote_id)}. "
                             f"In dieser Prüfung ist nur die Vorlage {self.fixed_vote_id} erlaubt.")
        if isinstance(vote_id, str) and vote_id in self.votes:
            return self.votes[vote_id]
        if self._recent_ids:
            hint = f"Gültige IDs aus der letzten Suche: {', '.join(self._recent_ids)}."
        else:
            hint = "Rufe zuerst suche_vorlagen auf, um gültige IDs zu erhalten."
        raise ValueError(f"Unbekannte Vorlagen-ID {_shown(vote_id)}. {hint}")

    def passages_for(self, vote_id: str) -> List[Dict[str, Any]]:
        """The vote's passages of all searches so far: original text, no duplicates, first found first, capped."""
        return [dict(p) for p in self._pool.get(vote_id, [])[:settings.MAX_PASSAGES]]

    def recheck_passages(self, vote_id: str, checked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The passages of the second check: the pooled passages `checked` does not contain (by key) first, in order
        of discovery, then `checked` itself; at most `MAX_PASSAGES`. Empty if the pool holds nothing new."""
        known = {_passage_key(p) for p in checked}
        fresh = [dict(p) for p in self._pool.get(vote_id, []) if _passage_key(p) not in known]
        if not fresh:
            return []
        return (fresh + [dict(p) for p in checked])[:settings.MAX_PASSAGES]

    def run(self, tool: str, args: dict, *, claim: Optional[str] = None) -> Observation:
        """Runs a search tool. Rejected calls and exceptions of the index or engine come back as an observation
        with `error`; `data["error_kind"]` tells `ERROR_INVALID_ARGS` (the model's mistake) from a tool failure.

        `claim` is the claim under check: `suche_passagen` searches with it (the graph always passes it); without it
        that tool fails technically."""
        if not isinstance(tool, str) or tool not in _TOOLS:
            return self._rejected((tool if isinstance(tool, str) else repr(tool))[:60], _unknown_tool(tool))
        if tool in TERMINAL_TOOLS:
            return self._rejected(tool, f"{tool} beendet den Lauf und wird nicht als Suchwerkzeug ausgeführt.")
        if self.fixed_vote_id is not None and tool == "suche_vorlagen":
            return self._rejected(tool, f"suche_vorlagen ist nicht verfügbar: Die Vorlage {self.fixed_vote_id} "
                                        "ist fest vorgegeben.")
        try:
            clean = validate_args(tool, args)
            if "vorlage_id" in clean:
                self.check_vote_id(clean["vorlage_id"])
        except ValueError as exc:
            return self._rejected(tool, str(exc))
        try:
            return self._handlers[tool](clean, claim)
        except Exception as exc:  # index or engine failed: reported, never raised into the graph
            detail = f"{type(exc).__name__}: {str(exc)[:ERROR_CHARS]}"
            return Observation(tool, f"Fehler: Das Werkzeug {tool} ist ausgefallen ({type(exc).__name__}).",
                               {"error_kind": ERROR_TOOL_FAILURE, "exception": type(exc).__name__}, detail)

    @staticmethod
    def _rejected(tool: str, message: str) -> Observation:
        return Observation(tool, f"Fehler: {message}", {"error_kind": ERROR_INVALID_ARGS}, message)

    def _search_votes(self, args: dict, claim: Optional[str]) -> Observation:
        candidates = self.backend.search_votes(args["anfrage"], k=VOTE_CANDIDATES)
        self._recent_ids = [c.vote.vote_id for c in candidates]
        assessment = guard.assess(candidates)
        if not candidates:
            return Observation("suche_vorlagen", "Keine Vorlage gefunden. Formuliere die Anfrage anders.",
                               {"candidates": [], "assessment": assessment.to_data()})
        lines = [f"Gefundene Vorlagen, beste zuerst. Score = ähnlichste Textstelle der Vorlage, Treffer = Textstellen "
                 f"der Vorlage unter den {TOP_CHUNKS} ähnlichsten."]
        for rank, found in enumerate(candidates, 1):
            lines.append(f"{rank}. {found.vote.vote_id} | {_display_date(found.vote.date)} | {found.vote.title} | "
                         f"Score {found.score:.3f} | Treffer {found.hits} | Seite {found.best_page} | "
                         f"«{_preview(found.snippet, SNIPPET_PREVIEW_CHARS)}»")
        lines.append(guard.hint(assessment))
        return Observation("suche_vorlagen", "\n".join(lines),
                           {"candidates": [self._candidate_data(c) for c in candidates],
                            "assessment": assessment.to_data()})

    @staticmethod
    def _candidate_data(found: VoteCandidate) -> dict:
        return {"vote_id": found.vote.vote_id, "title": found.vote.title, "date": found.vote.date,
                "score": found.score, "hits": found.hits, "best_page": found.best_page}

    def _search_passages(self, args: dict, claim: Optional[str]) -> Observation:
        """The proven search with the claim runs over the vote's whole booklet; only the vote's pages are pooled."""
        if not isinstance(claim, str) or not claim.strip():
            message = "Behauptung fehlt: suche_passagen sucht mit dem Wortlaut der Behauptung."
            return Observation("suche_passagen", f"Fehler: {message}", {"error_kind": ERROR_TOOL_FAILURE}, message)
        vote = self.votes[args["vorlage_id"]]
        found = self.backend.proven_passages(vote.vote_id, claim)
        return self._pooled("suche_passagen", "bewährte Suche mit der Behauptung", vote, found)

    def _search_passages_by_vector(self, args: dict, claim: Optional[str]) -> Observation:
        found = self.backend.vector_passages(args["vorlage_id"], args["anfrage"], k=VECTOR_PASSAGES)
        return self._pooled("suche_passagen_vektor", "Vektorsuche", self.votes[args["vorlage_id"]], found)

    def _pooled(self, tool: str, label: str, vote: VoteRef, found: List[Dict[str, Any]]) -> Observation:
        """Pools the passages on the pages of `vote`; a passage of another page is shown but never pooled."""
        keyed = [(_passage_key(p), dict(p)) for p in found]  # fails before anything enters the pool
        pool = self._pool.setdefault(vote.vote_id, [])
        seen = self._seen.setdefault(vote.vote_id, set())
        limit = POOL_LIMIT
        vote_pages = set(vote.pages)
        shown, new, dropped, outside = [], 0, 0, 0
        for passage_key, passage in keyed:
            if passage_key[0] not in vote_pages:
                status, outside = OUTSIDE_STATUS, outside + 1
            elif passage_key in seen:
                status = "schon im Pool"
            elif len(pool) >= limit:
                status, dropped = "Pool voll, nicht aufgenommen", dropped + 1
            else:
                seen.add(passage_key)
                pool.append(passage)
                status, new = "neu", new + 1
            shown.append({"page_number": passage_key[0], "section": passage.get("section"), "status": status})
        if not keyed:
            head = f"Keine Passagen aus {vote.vote_id} gefunden ({label})."
        else:
            head = f"Passagen aus {vote.vote_id} ({label}): {len(keyed)} gefunden, {new} neu im Pool."
        head += f" Pool dieser Vorlage: {len(pool)} Passagen; geprüft werden höchstens {settings.MAX_PASSAGES}."
        if dropped:
            head += f" {dropped} neue Passagen passten nicht mehr in den Pool und werden nicht geprüft."
        if outside:
            head += f" {outside} Passagen von anderen Vorlagen wurden nicht aufgenommen."
        lines = [head]
        for rank, ((_, passage), info) in enumerate(zip(keyed, shown), 1):
            section = f" ({info['section']})" if info["section"] else ""
            lines.append(f"{rank}. Seite {info['page_number']}{section}, {info['status']}: "
                         f"«{_preview(passage['text'], PASSAGE_PREVIEW_CHARS)}»")
        return Observation(tool, "\n".join(lines),
                           {"vote_id": vote.vote_id, "found": len(keyed), "new": new, "dropped": dropped,
                            "outside": outside, "pool_size": len(pool), "passages": shown})

