"""Regression set of the agent evaluation and the paired comparison against B0.

The 42 observed dev cases (`data/genai/ost_june_dev.jsonl`: 15 DE, 16 FR, 11 IT claims) run through the agent with the
source given, and a fresh B0 run of the production path (`engine.verify_claim(vote=None)` on the case's booklet) is the
reference, run on the same day. The agent's search is restricted to the given source: the vote is fixed in its tools.

* A German booklet is known to the index: the fixed vote is the vote of the case's title. A French or Italian booklet
  is not (the index is German only): the fixed vote is the whole booklet (`agent_ui.vote_for_selection` decides, as in
  the app). A vote that cannot be built is an error naming the case, never a dropped case.
* `compare_paired` compares the two runs case by case. A correct result beats a wrong one, and a technical error (or a
  case that did not run) is worse than any other result, a neutral one included. The criterion: at most one case net
  worse (`net_worse` = worse - better), and every changed case is listed with its reason.

The dataset's integer label (`entailment_label`) is mapped to the outcome vocabulary with `graph.LABEL_OUTCOMES`, the
mapping of the agent's own verdicts.
"""
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import config
from src import agent_ui
from src.agent.graph import LABEL_OUTCOMES
from src.agent.source_index import VoteRef

REGRESSION_PATH = config.DATA_DIR / "genai" / "ost_june_dev.jsonl"
REGRESSION_CASES = 42
REGRESSION_ARMS = ("regression-b0", "regression-agent")
LANGUAGES = ("de", "fr", "it")
TECHNICAL = "technischer_fehler"
NOT_RUN = "nicht_gelaufen"  # eval_runner.NOT_RUN: the campaign did not run the case (cap or stop)

BETTER, WORSE, EQUAL = "besser", "schlechter", "gleich"  # `ChangedCase.direction`
_CORRECT, _WRONG, _NO_RESULT = 2, 1, 0  # the order of a result: correct > wrong > technical error or not run
MAX_NET_WORSE = 1


@dataclass(frozen=True)
class RegressionCase:
    id: str
    claim: str
    claim_language: str
    label: int  # gold: 0 entailment, 1 neutral, 2 contradiction (the dataset's `entailment_label`)
    source_id: str  # the booklet, "2026-06-14_fr"
    vote_title: Optional[str]  # German booklets only (the index knows them); None: the whole booklet


@dataclass(frozen=True)
class CaseOutcome:
    """The result of one regression arm on one case; `label` is the gold label of the case."""
    id: str
    arm: str
    outcome: str
    label: int
    error: Optional[str] = None  # set for a technical error
    vote_id: Optional[str] = None  # the vote the check ran on (agent arm)
    note: Optional[str] = None  # the agent's own remark (e.g. a discarded verdict)
    tokens: int = 0
    latency_ms: float = 0.0

    @property
    def correct(self) -> bool:
        return self.outcome == LABEL_OUTCOMES.get(self.label)


@dataclass(frozen=True)
class ChangedCase:
    id: str
    b0: str  # the outcomes
    agent: str
    direction: str  # BETTER, WORSE or EQUAL (both wrong in a different way)
    reason: str  # German


@dataclass(frozen=True)
class PairedComparison:
    cases: int
    b0_correct: int
    agent_correct: int
    better: int
    worse: int
    net_worse: int  # worse - better
    changed: List[ChangedCase]  # every case whose outcome differs, in the order of the cases
    passes: bool  # net_worse <= MAX_NET_WORSE


# --- cases ------------------------------------------------------------------------------------------------------------

def booklet_language(source_id: str) -> str:
    """The language of a booklet from its file stem ("2026-06-14_fr" -> "fr")."""
    return source_id.rsplit("_", 1)[-1]


def _case(record: Dict[str, Any], line: int) -> RegressionCase:
    try:
        source_id = Path(str(record["booklet_file"])).stem
        label = record["entailment_label"]
        if type(label) is not int or label not in LABEL_OUTCOMES or record["claim_language"] not in LANGUAGES:
            raise ValueError("Label oder Sprache")
        title = record["vote"] if booklet_language(source_id) == "de" else None
        if title is not None and not (isinstance(title, str) and title.strip()):
            raise ValueError("Vorlagentitel")
        return RegressionCase(id=str(record["id"]), claim=str(record["claim"]),
                              claim_language=record["claim_language"], label=label, source_id=source_id,
                              vote_title=title)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{REGRESSION_PATH.name}, Zeile {line}: unbrauchbarer Fall ({exc})") from None


def load_regression_cases(path: Path = REGRESSION_PATH) -> List[RegressionCase]:
    """The 42 dev cases of the regression set. ValueError for a file with another number of cases, a repeated id or
    a case without a usable label, language or booklet: a regression set is never silently smaller."""
    cases = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            cases.append(_case(json.loads(line), number))
    if len(cases) != REGRESSION_CASES or len({case.id for case in cases}) != len(cases):
        raise ValueError(f"Die Regressionsmenge hat {len(cases)} Fälle (erwartet {REGRESSION_CASES}, ohne "
                         "doppelte IDs).")
    return cases


def fixed_vote_for(case: RegressionCase, votes: Mapping[str, VoteRef], booklets_dir: Path,
                   catalog: Optional[Mapping[str, Mapping[str, Any]]]) -> VoteRef:
    """The vote the agent is given for `case`: the vote of its title (German booklet) or the whole booklet (French or
    Italian: `agent_ui.vote_for_selection`, which reads the PDF's page count). ValueError naming the case if the vote
    cannot be built (unknown title, booklet missing or not in the catalog)."""
    try:
        return agent_ui.vote_for_selection(votes, case.source_id, case.vote_title, booklets_dir=Path(booklets_dir),
                                           catalog=catalog if isinstance(catalog, Mapping) else None)
    except ValueError as exc:
        raise ValueError(f"Fall {case.id}: Die feste Vorlage lässt sich nicht bilden ({exc})") from None


# --- outcomes as records ----------------------------------------------------------------------------------------------

def outcome_record(run: CaseOutcome, requests: int) -> dict:
    """The line of `cases.jsonl` for `run`."""
    return {"id": run.id, "arm": run.arm, "outcome": run.outcome, "label": run.label, "error": run.error,
            "vote_id": run.vote_id, "note": run.note, "tokens": run.tokens, "latency_ms": run.latency_ms,
            "requests": requests}


def outcome_from_record(record: Mapping[str, Any]) -> CaseOutcome:
    return CaseOutcome(id=record["id"], arm=record["arm"], outcome=record["outcome"], label=record["label"],
                       error=record.get("error"), vote_id=record.get("vote_id"), note=record.get("note"),
                       tokens=record.get("tokens", 0), latency_ms=record.get("latency_ms", 0.0))


# --- comparison -------------------------------------------------------------------------------------------------------

def _rank(run: CaseOutcome) -> int:
    if run.outcome in (TECHNICAL, NOT_RUN):
        return _NO_RESULT
    return _CORRECT if run.correct else _WRONG


def _status(run: CaseOutcome) -> str:
    if run.outcome == NOT_RUN:
        return "nicht gelaufen"
    if run.outcome == TECHNICAL:
        return "technischer Fehler"
    return "richtig" if run.correct else "falsch"


def _reason(b0: CaseOutcome, agent: CaseOutcome, direction: str) -> str:
    text = (f"Soll {LABEL_OUTCOMES.get(b0.label, b0.label)}. B0: {b0.outcome} ({_status(b0)}), "
            f"Agent: {agent.outcome} ({_status(agent)}); {direction}.")
    for who, run in (("Agent", agent), ("B0", b0)):
        detail = run.error or run.note
        if detail:
            text += f" {who}: {detail}"
    return text


def _by_id(runs: Sequence[CaseOutcome], side: str) -> Dict[str, CaseOutcome]:
    found = {run.id: run for run in runs}
    if len(found) != len(runs):
        raise ValueError(f"{side}: Fälle kommen doppelt vor.")
    return found


def compare_paired(b0: Sequence[CaseOutcome], agent: Sequence[CaseOutcome]) -> PairedComparison:
    """Compares the agent's outcomes with B0's, case by case. ValueError unless both cover exactly the same cases with
    the same gold labels (a case missing on one side is never dropped). A case is changed if its outcome differs; it is
    better or worse by the order correct > wrong > technical error or not run."""
    left, right = _by_id(b0, "B0"), _by_id(agent, "Agent")
    if set(left) != set(right):
        missing = sorted(set(left) ^ set(right))
        raise ValueError(f"B0 und Agent decken nicht dieselben Fälle ab ({len(missing)} Unterschiede, z. B. "
                         f"{', '.join(missing[:3])}).")
    changed: List[ChangedCase] = []
    for case_id in (run.id for run in b0):
        before, after = left[case_id], right[case_id]
        if before.label != after.label:
            raise ValueError(f"Fall {case_id}: B0 und Agent nennen verschiedene Soll-Labels.")
        if before.outcome == after.outcome:
            continue
        direction = BETTER if _rank(after) > _rank(before) else WORSE if _rank(after) < _rank(before) else EQUAL
        changed.append(ChangedCase(case_id, before.outcome, after.outcome, direction,
                                   _reason(before, after, direction)))
    better = sum(1 for change in changed if change.direction == BETTER)
    worse = sum(1 for change in changed if change.direction == WORSE)
    return PairedComparison(cases=len(left), b0_correct=sum(run.correct for run in left.values()),
                            agent_correct=sum(run.correct for run in right.values()), better=better, worse=worse,
                            net_worse=worse - better, changed=changed, passes=worse - better <= MAX_NET_WORSE)
