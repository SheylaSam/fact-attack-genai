"""Guard: assesses the vote candidates of a claim with fixed thresholds.

The assessment is deterministic and works on the ranking of `suche_vorlagen` for the claim's own wording. It tells
the model (and later the graph) whether one vote clearly fits ("eindeutig"), several votes are on par
("mehrdeutig") or none fits ("keine").
"""
import datetime
from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Tuple

from src.agent import settings
from src.agent.source_index import VoteCandidate, VoteRef

Kind = Literal["eindeutig", "mehrdeutig", "keine"]

CLOSE_RANGE = 4  # at most this many ranks can be close (the most options `rueckfrage` takes)
TITLE_CHARS = 90  # a title in the question is cut here


@dataclass(frozen=True)
class Thresholds:
    score_margin: float
    min_hits_ratio: float
    none_below: Optional[float]


def current_thresholds() -> Thresholds:
    return Thresholds(settings.GUARD_SCORE_MARGIN, settings.GUARD_MIN_HITS_RATIO, settings.GUARD_NONE_BELOW)


@dataclass(frozen=True)
class Assessment:
    kind: Kind
    top: Optional[str]  # vote id of rank 1
    close: Tuple[str, ...]  # rank order, top included; at least 2 exactly when kind == "mehrdeutig"
    candidates: Tuple[VoteCandidate, ...]
    thresholds: Thresholds

    def to_data(self) -> dict:
        t = self.thresholds
        return {"kind": self.kind, "top": self.top, "close": list(self.close),
                "thresholds": {"score_margin": t.score_margin, "min_hits_ratio": t.min_hits_ratio,
                               "none_below": t.none_below}}


def assess(candidates: Sequence[VoteCandidate], thresholds: Optional[Thresholds] = None) -> Assessment:
    """The candidates must be in rank order (best first), as `suche_vorlagen` delivers them."""
    t = thresholds or current_thresholds()
    ranked = tuple(candidates)
    if not ranked:
        return Assessment("keine", None, (), ranked, t)
    top = ranked[0]
    if t.none_below is not None and top.score < t.none_below:
        return Assessment("keine", top.vote.vote_id, (), ranked, t)
    close = tuple(c.vote.vote_id for c in ranked[:CLOSE_RANGE]
                  if c is top or (top.score - c.score < t.score_margin and c.hits >= t.min_hits_ratio * top.hits))
    return Assessment("mehrdeutig" if len(close) >= 2 else "eindeutig", top.vote.vote_id, close, ranked, t)


def _date(date: str) -> str:
    try:
        return datetime.date.fromisoformat(date).strftime("%d.%m.%Y")
    except ValueError:
        return date


def _option(vote: VoteRef) -> str:
    title = vote.title if len(vote.title) <= TITLE_CHARS else vote.title[:TITLE_CHARS].rstrip() + "…"
    return f"«{title}» ({_date(vote.date)})"


def question(options: Sequence[VoteRef]) -> str:
    """The German question that names the options by title and date."""
    names = [_option(vote) for vote in options]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " oder " + names[-1]
    return f"Welche Vorlage ist gemeint: {listed}?"


def hint(assessment: Assessment) -> str:
    """The German line that ends the `suche_vorlagen` observation."""
    if assessment.kind == "eindeutig":
        return f"Einschätzung: {assessment.top} passt klar am besten."
    if assessment.kind == "mehrdeutig":
        return (f"Einschätzung: mehrdeutig, {', '.join(assessment.close)} liegen gleichauf. Lässt die Behauptung "
                "nicht erkennen, welche gemeint ist, beende mit rueckfrage und diesen Optionen.")
    return "Einschätzung: keine passende Vorlage. Beende mit quelle_fehlt."
