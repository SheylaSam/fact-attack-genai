"""Text of the agent CLI: the numbered step list, the result and its JSON form. Display only; the logic is in graph.py.

A step is what the user sees as one line: a decision together with its action (the trace has a `decide` and an `act`
event for it), or one of the closing nodes (verification, evidence check, result). Marks: ✓ went through, ? asked the
person back, ✗ went wrong (an invalid action that the agent recovered from is marked too; the error text is shown).
"""
import dataclasses
import datetime
import json
import shlex
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from src.agent.trace import TraceEvent

if TYPE_CHECKING:  # the graph needs langgraph (requirements-agent.txt); formatting only reads the result's fields
    from src.agent.graph import AgentResult
    from src.agent.source_index import VoteRef

OK, QUESTION, FAILED = "✓", "?", "✗"
ARG_CHARS = 50  # one argument value in the step list; the trace file has it whole
TOP_CANDIDATES = 3  # vote candidates of suche_vorlagen in the step list; the trace keeps all of them
INDENT = " " * 6  # under the text that follows "NN. ✓ "
NODE_LABELS = {"start": "Start", "decide": "Entscheidung", "act": "Aktion", "ask": "Rückfrage", "verify": "Prüfung", "check_evidence": "Belegprüfung",
               "nachsuche": "Nachsuche", "finish": "Abschluss"}
OUTCOME_LABELS = {"gestuetzt": "Gestützt", "widersprochen": "Widersprochen", "nicht_belegt": "Nicht belegt",
                  "rueckfrage": "Rückfrage", "quelle_fehlt": "Quelle fehlt", "technischer_fehler": "Technischer Fehler"}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _flat(text: Any) -> str:
    return " ".join(str(text).split())


def _duration(milliseconds: float) -> str:
    return f"{milliseconds:.0f} ms" if milliseconds < 1000 else f"{milliseconds / 1000:.1f} s"


def _display_date(date: str) -> str:
    try:
        return datetime.date.fromisoformat(date).strftime("%d.%m.%Y")
    except ValueError:
        return date


def _groups(events: List[TraceEvent]) -> List[List[TraceEvent]]:
    """The events of one decision (decide + act, same step) together; every other event is a step of its own."""
    groups: List[List[TraceEvent]] = []
    for item in events:
        last = groups[-1][-1] if groups else None
        if item.node == "act" and last is not None and last.node == "decide" and last.step == item.step:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _mark(group: List[TraceEvent]) -> str:
    if any(item.error for item in group):
        return FAILED
    return QUESTION if any(item.tool == "rueckfrage" for item in group) else OK


def _label(group: List[TraceEvent]) -> str:
    first = group[0]
    if first.node == "decide":
        return next((item.tool for item in reversed(group) if item.tool), NODE_LABELS["decide"])
    return NODE_LABELS.get(first.node, first.node)


def _arguments(group: List[TraceEvent]) -> str:
    args = next((item.args for item in group if item.args), {})
    return ", ".join(f"{name}={_cut(json.dumps(value, ensure_ascii=False, default=str), ARG_CHARS)}"
                     for name, value in args.items())


def _cost(group: List[TraceEvent]) -> str:
    parts = [_duration(sum(item.duration_ms for item in group))]
    prompt, completion = sum(item.tokens_prompt for item in group), sum(item.tokens_completion for item in group)
    if prompt or completion:
        parts.append(f"{prompt}+{completion} Tokens")
    return ", ".join(parts)


def _score(value: Any) -> str:
    return f"{value:.3f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def _candidates(data: Any) -> Optional[str]:
    """The best vote candidates of a suche_vorlagen event (its `data`), or None if it has none."""
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return None
    best = "; ".join(f"{found.get('vote_id')} (Score {_score(found.get('score'))}, {found.get('hits')} Treffer)"
                     for found in candidates[:TOP_CANDIDATES] if isinstance(found, dict))
    rest = len(candidates) - TOP_CANDIDATES
    return f"{len(candidates)} Vorlagen, beste: {best}" + (f"; {rest} weitere" if rest > 0 else "")


def _summary(group: List[TraceEvent]) -> str:
    """The action's summary (for suche_vorlagen: its best candidates), else that of the group's last event."""
    act = next((item for item in group if item.node == "act"), None)
    if act is None:
        return _flat(group[-1].summary)
    return (act.tool == "suche_vorlagen" and _candidates(act.data)) or _flat(act.summary)


def format_steps(events: List[TraceEvent]) -> str:
    """The run as a numbered list, one step per line: mark, tool or node, arguments; below it the summary (for
    suche_vorlagen the best candidates with score and hits) with time and tokens, and the error text if there is one."""
    groups = _groups(events)
    if not groups:
        return "Keine Schritte aufgezeichnet."
    width = max(len(_label(group)) for group in groups)
    lines = []
    for number, group in enumerate(groups, 1):
        head = f"{number:>2}. {_mark(group)} {_label(group):<{width}}"
        arguments = _arguments(group)
        lines.append(f"{head}  {arguments}" if arguments else head.rstrip())
        summary = _summary(group)
        lines.append(f"{INDENT}{summary} ({_cost(group)})" if summary else f"{INDENT}({_cost(group)})")
        for error in dict.fromkeys(item.error for item in group if item.error):
            lines.append(f"{INDENT}Fehler: {_flat(error)}")
    return "\n".join(lines)


def _vote_line(vote: "VoteRef") -> str:
    return f"{vote.title} ({_display_date(vote.date)}, {vote.vote_id})"


def answer_commands(claim: str, options: Sequence["VoteRef"], extra_args: Sequence[str] = ()) -> List[str]:
    """One command per option that answers a question with a new check of the claim for that vote."""
    suffix = "".join(f" {arg}" for arg in extra_args)
    return [f"python -m src.agent {shlex.quote(claim)} --quelle {option.vote_id}{suffix}" for option in options]


def format_result(result: "AgentResult", *, claim: Optional[str] = None, answer_args: Sequence[str] = ()) -> str:
    """The outcome with the vote, the question and options, the evidence quotes with their pages, the reasoning, a
    note and the error, as far as the result has them. Quotes are shown on one line each (PDF line breaks removed).
    With the `claim`, a question also lists the commands that answer it (`answer_args`: the run options to repeat)."""
    lines = [f"Ergebnis: {OUTCOME_LABELS.get(result.outcome, result.outcome)} ({result.outcome})"]
    if result.vote is not None:
        lines.append(f"Vorlage: {_vote_line(result.vote)}")
    if result.question:
        lines.append(f"Rückfrage: {result.question}")
    if result.options:
        lines.append("Optionen:")
        lines += [f"  - {_vote_line(option)}" for option in result.options]
        if claim is not None and result.outcome == "rueckfrage":
            lines.append("Antworten mit einer neuen Prüfung:")
            lines += [f"  {command}" for command in answer_commands(claim, result.options, answer_args)]
    if result.evidence:
        lines.append("Belege:")
        lines += [f"  - S. {item.page}: «{_flat(item.quote)}»" for item in result.evidence]
    if result.reasoning:
        lines.append(f"Begründung: {_flat(result.reasoning)}")
    if result.note:
        lines.append(f"Hinweis: {result.note}")
    if result.error:
        lines.append(f"Fehler: {_flat(result.error)}")
    prompt, completion = (sum(getattr(item, name) for item in result.events)
                          for name in ("tokens_prompt", "tokens_completion"))
    if prompt or completion:
        lines.append(f"Tokens: {prompt} Prompt, {completion} Antwort")
    return "\n".join(lines)


def _vote_dict(vote: Optional["VoteRef"]) -> Optional[Dict[str, Any]]:
    return dataclasses.asdict(vote) if vote is not None else None


def result_to_dict(result: "AgentResult") -> Dict[str, Any]:
    """The whole `AgentResult` as plain JSON values (`--json`)."""
    prediction = result.prediction
    return {
        "outcome": result.outcome,
        "vote": _vote_dict(result.vote),
        "evidence": [{"page": item.page, "quote": item.quote} for item in result.evidence],
        "question": result.question,
        "options": [_vote_dict(option) for option in result.options],
        "reasoning": result.reasoning,
        "note": result.note,
        "error": result.error,
        "prediction": prediction.model_dump(mode="json") if prediction is not None else None,
        "events": [dataclasses.asdict(item) for item in result.events],
        "steps": result.steps,
        "asked_by": result.asked_by,
    }
