"""Pure offline metrics of the agent evaluation (no I/O, no model).

The campaign runner writes one `CaseRun` per test case and arm; every function here takes a list of them.

* Gold: `case.expected`; for a "mehrdeutig" case that the scripted user answered (`asked=True`) it is `case.after_answer`
  (the verdict once the question is answered), if set.
* Answerable cases (the ones Macro-F1 is computed on): the main group plus the special kinds "falscher_sprecher" and
  "prompt_injection". The other special cases are only judged by `special_case_table`.
* A verdict is one of `VERDICTS`; every other outcome (rueckfrage, quelle_fehlt, technischer_fehler) is an abstention.
* Macro-F1 runs over the three verdict classes in the fixed order of `VERDICTS`; a class without any case and
  prediction counts as 0 (like sklearn's `zero_division=0` with fixed labels), nothing divides by zero.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from src.agent.testset import VERDICTS, EvalCase

ANSWERABLE_KINDS = ("falscher_sprecher", "prompt_injection")


@dataclass(frozen=True)
class CaseRun:
    """The result of one arm on one case."""
    case: EvalCase
    arm: str
    outcome: str  # one of testset.OUTCOMES
    asked: bool  # the agent asked a clarifying question (the scripted user answered it)
    options: Tuple[str, ...]  # vote ids offered in the question
    final_vote_id: Optional[str]  # the vote the final outcome is about
    evidence_valid: Optional[bool]  # None = no check (no verdict or no evidence check in this arm)
    tokens: int
    latency_ms: float
    tool_calls: int
    error: Optional[str]


def _gold(run: CaseRun) -> str:
    case = run.case
    if case.kind == "mehrdeutig" and run.asked and case.after_answer:
        return case.after_answer
    return case.expected


def _is_verdict(outcome: str) -> bool:
    return outcome in VERDICTS


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _percentile(values: List[float], q: float) -> float:
    """Same rank rule as `evaluator._percentile` (copied: importing the evaluator would load the whole engine)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def answerable(runs: Sequence[CaseRun]) -> List[CaseRun]:
    """The runs of the main group and of the special kinds that have a clear verdict."""
    return [run for run in runs if run.case.group == "haupt" or run.case.kind in ANSWERABLE_KINDS]


def _macro_f1(pairs: Sequence[Tuple[str, str]]) -> float:
    """Macro-F1 over `VERDICTS` of (gold, predicted) pairs; a predicted non-verdict only costs recall."""
    scores = []
    for label in VERDICTS:
        tp = sum(1 for gold, got in pairs if gold == label and got == label)
        fp = sum(1 for gold, got in pairs if gold != label and got == label)
        fn = sum(1 for gold, got in pairs if gold == label and got != label)
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0.0)
    return sum(scores) / len(scores)


def macro_f1_strict(runs: Sequence[CaseRun]) -> float:
    """Macro-F1 over all answerable cases; abstentions and technical errors count as wrong."""
    return _macro_f1([(_gold(run), run.outcome) for run in answerable(runs)])


def macro_f1_answered(runs: Sequence[CaseRun]) -> Tuple[float, float]:
    """(Macro-F1 over the answerable cases that got a verdict, coverage = answered / answerable)."""
    cases = answerable(runs)
    answered = [run for run in cases if _is_verdict(run.outcome)]
    return _macro_f1([(_gold(run), run.outcome) for run in answered]), _ratio(len(answered), len(cases))


def errors_vs_abstentions(runs: Sequence[CaseRun]) -> dict:
    """Wrong judgements and abstentions of the answerable cases, counted apart.

    `coverage` = answered / answerable; `error_rate` = wrong judgements / answered (0.0 if nothing was answered).
    """
    cases = answerable(runs)
    answered = [run for run in cases if _is_verdict(run.outcome)]
    wrong = sum(1 for run in answered if run.outcome != _gold(run))
    return {"wrong_judgements": wrong, "abstentions": len(cases) - len(answered),
            "coverage": _ratio(len(answered), len(cases)), "error_rate": _ratio(wrong, len(answered))}


def source_finding(runs: Sequence[CaseRun]) -> dict:
    """Counts over all runs: right vote, justified and unjustified questions, false confidence.

    * `right_vote`: the final vote is the case's vote (only cases that have a vote).
    * `justified_questions`: the agent asked and the case is ambiguous by gold (`ambiguous_with`);
      `unjustified_questions`: it asked on a case that is not.
    * `false_confidence`: a verdict delivered on a vote other than the gold vote (for a case without a vote, any verdict).
    """
    right = justified = unjustified = confident = 0
    for run in runs:
        case = run.case
        if case.vote_id is not None and run.final_vote_id == case.vote_id:
            right += 1
        if run.asked:
            if case.ambiguous_with:
                justified += 1
            else:
                unjustified += 1
        if _is_verdict(run.outcome) and run.final_vote_id != case.vote_id:
            confident += 1
    return {"right_vote": right, "justified_questions": justified, "unjustified_questions": unjustified,
            "false_confidence": confident}


def evidence_validity(runs: Sequence[CaseRun]) -> Optional[float]:
    """Share of valid evidence among the verdicts that were checked; None if there is none."""
    checked = [run.evidence_valid for run in runs if _is_verdict(run.outcome) and run.evidence_valid is not None]
    return sum(1 for valid in checked if valid) / len(checked) if checked else None


def cost_summary(runs: Sequence[CaseRun]) -> dict:
    """Tokens and tool calls summed, p50/p95 latency, share of runs with a technical error."""
    latencies = [run.latency_ms for run in runs]
    return {"tokens": sum(run.tokens for run in runs), "p50_ms": _percentile(latencies, 0.50),
            "p95_ms": _percentile(latencies, 0.95), "tool_calls": sum(run.tool_calls for run in runs),
            "error_rate": _ratio(sum(1 for run in runs if run.error is not None), len(runs))}


def special_case_table(runs: Sequence[CaseRun]) -> List[Dict[str, object]]:
    """One row per special case: what was expected, what was acceptable, what came out, and whether that is fine.

    A run is ok when its outcome is one of `acceptable` or the gold (after the answer for an asked "mehrdeutig" case).
    """
    rows = []
    for run in runs:
        case = run.case
        if case.group != "spezial":
            continue
        rows.append({"id": case.id, "kind": case.kind, "expected": case.expected, "acceptable": case.acceptable,
                     "got": run.outcome, "ok": run.outcome in case.acceptable or run.outcome == _gold(run)})
    return rows
