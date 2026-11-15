"""View data of the agent check for the app: step rows, outcome view, card input, the live stream. Pure functions
(except `vote_for_selection`, which may read a booklet's page count), no Streamlit; the app draws what they return.

Every German text of this module is a module-level constant (or a value of one) and is listed in `TEXT_KEYS`, so the
app translates it with `t(...)` and `tests/test_ui_i18n.py` can check that each one has a French and an Italian row.
Texts that come from a run (event summaries, error messages, the result's error) are German as the agent wrote them
and are shown unchanged.

The agent layer (langgraph) is only imported for type hints: this module loads without it.
"""
from dataclasses import dataclass
from pathlib import Path
import queue
import threading
from typing import (TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Literal, Mapping, Optional, Sequence, Tuple, Union)

from src import config
from src.agent.trace import TraceEvent
from src.inference import EvidenceSource, PredictionResult

if TYPE_CHECKING:  # the agent graph needs langgraph (requirements-agent.txt)
    from src.agent.graph import AgentResult
    from src.agent.session import Pending
    from src.agent.source_index import VoteRef

# --- texts ---------------------------------------------------------------------------------------------------------
TITLE_ASK = "Rückfrage"
TITLE_DECIDE = "Entscheidung"
TITLE_RUNNING = "Nächster Schritt läuft …"
TOOL_TITLES = {
    "suche_vorlagen": "Vorlagen suchen",
    "suche_passagen": "Passagen suchen",
    "suche_passagen_vektor": "Passagen suchen (Vektor)",
    "pruefen": "Prüfung starten",
    "quelle_fehlt": "Quelle fehlt",
    "rueckfrage": TITLE_ASK,
    "teilaussage_fragen": TITLE_ASK,
}
NODE_TITLES = {
    "start": "Start",
    "verify": "Urteil von Apertus",
    "check_evidence": "Belege prüfen",
    "nachsuche": "Nachsuche",
    "finish": "Abbruch",
}
TITLE_SUPPORTED = "Vom Büchlein gestützt"
TEXT_SUPPORTED = "Das Büchlein enthält Aussagen, die diese Behauptung stützen."
TITLE_NOT_PROVEN = "Im Büchlein nicht belegt"
TEXT_NOT_PROVEN = "Im geprüften Kontext wurde keine Aussage gefunden, die diese Behauptung bestätigt oder widerlegt."
TITLE_CONTRADICTED = "Widerspricht dem Büchlein"
TEXT_CONTRADICTED = "Das Büchlein enthält Aussagen, die dieser Behauptung widersprechen."
TITLE_QUESTION = "Welche Vorlage ist gemeint?"
TEXT_QUESTION = "Mehrere Vorlagen passen zur Behauptung. Wähle die Vorlage, die du meinst; danach geht die Prüfung weiter."
TEXT_QUESTION_CHOOSE = "Wähle die Vorlage, die du meinst; danach geht die Prüfung weiter."  # the guard's mismatch
TITLE_STATEMENT = "Welche Teilaussage soll geprüft werden?"  # a compound claim (teilaussage_fragen)
TEXT_STATEMENT = ("Die Behauptung verbindet mehrere Aussagen, die einzeln verschieden ausfallen können. Wähle eine; "
                  "sie wird als neue Behauptung geprüft.")
TITLE_WHOLE_BOOKLET = "Gesamtes Büchlein"  # = source_index.WHOLE_BOOKLET_TITLE, the title of a "#0" vote
TITLE_UPLOAD = "Eigenes PDF"  # = uploads.UPLOAD_TITLE, the title of an own PDF's vote
TITLE_PICK_SOURCE = "Quelle selbst wählen"
TEXT_PICK_SOURCE = ("Die Suche über alle Vorlagen gibt es bisher nur für deutsche Behauptungen. Wähle die Quelle selbst; "
                    "mit gewählter Quelle wird die Behauptung in jeder Sprache geprüft.")
TITLE_NO_SOURCE = "Keine passende Vorlage gefunden"
TEXT_NO_SOURCE = "Zur Behauptung passt keine Vorlage. Wähle die Quelle selbst oder formuliere die Behauptung um."
TEXT_LAST_VOTE = "wie zuletzt: {v0}"  # the button of the person's last chosen vote; {v0} is its title
TITLE_TECHNICAL = "Die Prüfung konnte nicht abgeschlossen werden"
TEXT_TECHNICAL = "Die Prüfung wurde wegen eines technischen Fehlers abgebrochen; es gibt kein Urteil."

# plain-language step titles; the placeholders are filled from the trace data by `row_text`
TITLE_VOTES_MANY = "{n} passende Vorlagen gefunden"
TITLE_VOTES_ONE = "Passende Vorlage gefunden: {v0}"
TITLE_ANSWERED = "Du hast gewählt: {v0}, Abstimmung vom {date}"
TITLE_PASSAGES = "{n} Textstellen im Büchlein gefunden"
TITLE_VECTOR = "Nachsuche: {n} neue Textstellen"
TITLE_VERIFIED = "Apertus hat geprüft: {v0}"
TITLE_EVIDENCE_ALL = "Alle {n} Zitate stehen wörtlich im PDF"
TITLE_EVIDENCE_SOME = "{v0} von {n} Zitaten stehen im PDF"
TITLE_EVIDENCE_ONE = "Das Zitat steht wörtlich im PDF"
TITLE_UNCHECKED_ONE = "Ein Zitat ungeprüft übernommen"
TITLE_UNCHECKED_MANY = "{n} Zitate ungeprüft übernommen"
TITLE_EVIDENCE_NONE = "Kein Zitat zu prüfen"
TITLE_EVIDENCE_REJECTED = "Urteil verworfen: kein Zitat steht im PDF"
OUTCOME_WORDS = {0: "gestützt", 1: "nicht belegt", 2: "widersprochen"}  # NLI label -> word in a sentence

TEXT_KEYS: Tuple[str, ...] = tuple(dict.fromkeys((
    TITLE_VOTES_MANY, TITLE_VOTES_ONE, TITLE_ANSWERED, TITLE_PASSAGES, TITLE_VECTOR, TITLE_VERIFIED,
    TITLE_EVIDENCE_ALL, TITLE_EVIDENCE_ONE, TITLE_UNCHECKED_ONE, TITLE_UNCHECKED_MANY,
    TITLE_EVIDENCE_SOME, TITLE_EVIDENCE_NONE, TITLE_EVIDENCE_REJECTED, *OUTCOME_WORDS.values(),
    TITLE_ASK, TITLE_DECIDE, TITLE_RUNNING, *TOOL_TITLES.values(), *NODE_TITLES.values(),
    TITLE_SUPPORTED, TEXT_SUPPORTED, TITLE_NOT_PROVEN, TEXT_NOT_PROVEN, TITLE_CONTRADICTED, TEXT_CONTRADICTED,
    TITLE_QUESTION, TEXT_QUESTION, TEXT_QUESTION_CHOOSE, TITLE_STATEMENT, TEXT_STATEMENT,
    TITLE_WHOLE_BOOKLET, TITLE_UPLOAD, TITLE_PICK_SOURCE, TEXT_PICK_SOURCE, TITLE_NO_SOURCE, TEXT_NO_SOURCE,
    TEXT_LAST_VOTE, TITLE_TECHNICAL, TEXT_TECHNICAL)))

# outcome -> NLI label (the card's label); the same mapping as the agent graph's, without importing langgraph
OUTCOME_LABELS = {"gestuetzt": 0, "nicht_belegt": 1, "widersprochen": 2}


# --- step rows -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StepRow:
    mark: Literal["done", "ask", "error", "running"]
    title: str  # German sentence template (placeholders in braces); `row_text` translates and fills it
    detail: str  # the summary the agent wrote; shown unchanged
    error: Optional[str]
    kind: str = ""  # votes, ask, statement, passages, vector, verify, evidence, source_missing; "" = old title
    params: Tuple[Tuple[str, str], ...] = ()  # (name, value) for the placeholders of `title`



def _flat(text: Any) -> str:
    return " ".join(str(text).split())


def _asks(group: List[TraceEvent]) -> bool:
    """The decision ended in a question without an error: the guard did not run the action, or the model asked."""
    for item in group:
        if item.node == "act" and not item.error:
            guard_stopped = item.summary.startswith("Wächter:") and "guard" in item.data  # the action was not run
            if item.tool in ("rueckfrage", "teilaussage_fragen") or guard_stopped:  # the model's own questions
                return True
    return False


def _groups(events: Sequence[TraceEvent]) -> List[List[TraceEvent]]:
    """The events of one step together: decide + act of the same step, and the `ask` that follows a question."""
    groups: List[List[TraceEvent]] = []
    for item in events:
        last = groups[-1] if groups else None
        if last is not None and last[0].node == "decide" and last[-1].step == item.step:
            if (item.node == "act" and last[-1].node == "decide") or (item.node == "ask" and _asks(last)):
                last.append(item)
                continue
        groups.append([item])
    return groups


def _errors(group: List[TraceEvent]) -> Optional[str]:
    errors = list(dict.fromkeys(_flat(item.error) for item in group if item.error))
    return "; ".join(errors) or None


def _ask_detail(group: List[TraceEvent]) -> str:
    act = next((item for item in group if item.node == "act"), None)
    ask = next((item for item in group if item.node == "ask"), None)
    # the guard's question: say what it stopped, then how it went on; the model's own questions: the ask summary
    if act is not None and act.tool not in ("rueckfrage", "teilaussage_fragen"):
        return " · ".join(_flat(item.summary) for item in (act, ask) if item is not None and item.summary)
    return _flat((ask or act or group[-1]).summary)


def _title(group: List[TraceEvent]) -> str:
    first = group[0]
    if first.node == "decide":
        tool = next((item.tool for item in reversed(group) if item.tool), None)
        return TOOL_TITLES.get(tool, tool) if tool else TITLE_DECIDE
    return NODE_TITLES.get(first.node, first.node)


STEP_ICONS: Dict[str, Tuple[str, str]] = {  # kind -> (Font Awesome class, tone)
    "votes": ("fa-search", "blue"),
    "ask": ("fa-question", "yellow"),
    "statement": ("fa-question", "yellow"),
    "passages": ("fa-file-alt", "blue"),
    "vector": ("fa-layer-group", "blue"),
    "verify": ("fa-balance-scale", "blue"),
    "evidence": ("fa-check-double", "green"),
    "source_missing": ("fa-search-minus", "grey"),
    "error": ("fa-exclamation-triangle", "orange"),
    "running": ("fa-circle-notch", "grey"),
}


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sentence(group: List[TraceEvent]) -> Optional[Tuple[str, str, Tuple[Tuple[str, str], ...]]]:
    """(kind, title template, params) of a finished step from its trace data; None if the data is missing."""
    act = next((item for item in group if item.node == "act"), None)
    ask = next((item for item in group if item.node == "ask"), None)
    first = group[0]
    if first.node == "ask" or _asks(group):
        if any(item.tool == "teilaussage_fragen" for item in group):
            return "statement", TITLE_STATEMENT, ()
        answer = (ask.args or {}).get("antwort") if ask is not None else None
        if answer is None:
            return "ask", TITLE_QUESTION, ()
        option = next((o for o in (ask.data or {}).get("options") or []
                       if isinstance(o, dict) and o.get("vote_id") == answer), None)
        if option is None or not option.get("title"):
            return None
        return "ask", TITLE_ANSWERED, (("v0", str(option["title"])), ("date", str(option.get("date") or "")))
    data = (act.data if act is not None else first.data) or {}
    tool = act.tool if act is not None else None
    if first.node == "decide" and act is not None:
        if tool == "suche_vorlagen":
            candidates = data.get("candidates")
            if not isinstance(candidates, list):
                return None
            if not candidates:
                return "votes", TITLE_NO_SOURCE, ()
            if len(candidates) == 1:
                return "votes", TITLE_VOTES_ONE, (("v0", str(candidates[0].get("title", ""))),)
            return "votes", TITLE_VOTES_MANY, (("n", str(len(candidates))),)
        if tool == "suche_passagen" and _count(data.get("found")) is not None:
            return "passages", TITLE_PASSAGES, (("n", str(data["found"])),)
        if tool == "suche_passagen_vektor" and _count(data.get("new")) is not None:
            return "vector", TITLE_VECTOR, (("n", str(data["new"])),)
        if tool == "quelle_fehlt":
            return "source_missing", TITLE_NO_SOURCE, ()
        return None
    data = first.data or {}
    if first.node == "verify" and data.get("label") in OUTCOME_WORDS:
        return "verify", TITLE_VERIFIED, (("v0", OUTCOME_WORDS[data["label"]]),)
    if first.node == "check_evidence" and _count(data.get("valid")) is not None and _count(data.get("quotes")) is not None:
        valid, quotes = data["valid"], data["quotes"]
        if data.get("checked") is False and valid > 0:  # the evidence check was off: nothing was looked up in the PDF
            if valid == 1:
                return "evidence", TITLE_UNCHECKED_ONE, ()
            return "evidence", TITLE_UNCHECKED_MANY, (("n", str(valid)),)
        if quotes == 0:
            return "evidence", TITLE_EVIDENCE_NONE, ()
        if valid == 0:
            return "evidence", TITLE_EVIDENCE_REJECTED, ()
        if valid >= quotes == 1:
            return "evidence", TITLE_EVIDENCE_ONE, ()
        if valid >= quotes:
            return "evidence", TITLE_EVIDENCE_ALL, (("n", str(quotes)),)
        return "evidence", TITLE_EVIDENCE_SOME, (("v0", str(valid)), ("n", str(quotes)))
    return None


def row_text(row: StepRow, translate: Callable[[str], str], format_date: Callable[[str], str]) -> str:
    """The row's sentence in the UI language: the title template translated, every parameter value that is a known
    text translated, `date` formatted with `format_date`, then filled in."""
    values = {}
    for name, value in row.params:
        values[name] = format_date(value) if name == "date" else translate(value) if value in TEXT_KEYS else value
    text = translate(row.title)
    return text.format(**values) if values else text


def step_rows(events: Sequence[TraceEvent], *, running: bool = False) -> List[StepRow]:
    """One row per step of the run, in order. A decision and its action are one row. A question, whether the guard
    stopped the action or the model asked, is one `ask` row together with its `ask` event; the stopped action gets no
    checkmark. An error makes its row red and shows the message once (the closing error of a run repeats the one
    of the step that caused it). Plain `start` and `finish` events have no row. With `running` the row of the step in
    progress is added (a decision that has no action yet is that row)."""
    rows: List[StepRow] = []
    shown = set()  # the errors some row already shows
    groups = _groups(list(events))
    for number, group in enumerate(groups):
        first, error = group[0], _errors(group)
        if first.node in ("start", "finish"):
            if error is None or error in shown:
                continue
        if error is not None:
            row = StepRow("error", _title(group), _flat(group[-1].summary), error)
        elif first.node == "ask" or _asks(group):
            sentence = _sentence(group)
            kind, title, params = sentence if sentence else ("", TITLE_ASK, ())
            row = StepRow("ask", title, _ask_detail(group), None, kind, params)
        else:
            act = next((item for item in group if item.node == "act"), None)
            open_step = running and number == len(groups) - 1 and first.node == "decide" and act is None
            sentence = None if open_step else _sentence(group)
            kind, title, params = sentence if sentence else ("", _title(group), ())
            row = StepRow("running" if open_step else "done", title, _flat((act or group[-1]).summary), None,
                          kind, params)
        if error is not None:
            shown.add(error)
        rows.append(row)
    if running and (not rows or rows[-1].mark != "running"):
        rows.append(StepRow("running", TITLE_RUNNING, "", None))
    return rows


# --- outcome -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class OutcomeView:
    kind: Literal["verdict", "state"]  # a verdict about the claim, or a state in which there is none
    title: str
    text: str
    tone: Literal["ok", "warn", "error", "info"]


def asks_to_pick_source(result: Any) -> bool:
    """`quelle_fehlt` before any search because the claim's language needs a chosen source (the graph's `prepare`)."""
    return getattr(result, "outcome", None) == "quelle_fehlt" and any(
        item.node == "finish" and item.tool == "quelle_fehlt" and "sprache" in item.args for item in result.events)


def question_text(events: Sequence[TraceEvent]) -> str:
    """The text under the open question: "several votes fit" unless the latest question is the guard's mismatch
    (the claim clearly fits one vote, the action named another), which gets a neutral text. The guard's assessment
    sits in the stopped action's `guard` data while the question is open, and in the `ask` event's data after it."""
    for item in reversed(events):
        data = item.data or {}
        if item.node == "act" and "guard" in data:
            data = data["guard"] or {}
        elif item.node != "ask":
            continue
        return TEXT_QUESTION_CHOOSE if "chosen" in data else TEXT_QUESTION
    return TEXT_QUESTION


def statement_choices(turn: Any) -> List[str]:
    """The statements of a compound claim the model asks to choose from (`teilaussage_fragen`), one button each, in
    its order; empty for every other turn (a vote question, a `Pending`, a verdict, an exception, None). A chosen
    statement is checked as a new claim. They are the model's text: the app shows them escaped."""
    if getattr(turn, "outcome", None) != "rueckfrage":
        return []
    return [text for text in getattr(turn, "statements", None) or [] if isinstance(text, str)]


def outcome_view(result: "AgentResult") -> OutcomeView:
    """How the result is announced: verdicts for the three that judge the claim, states for the others."""
    outcome = result.outcome
    if outcome == "gestuetzt":
        return OutcomeView("verdict", TITLE_SUPPORTED, TEXT_SUPPORTED, "ok")
    if outcome == "widersprochen":
        return OutcomeView("verdict", TITLE_CONTRADICTED, TEXT_CONTRADICTED, "warn")
    if outcome == "nicht_belegt":
        return OutcomeView("verdict", TITLE_NOT_PROVEN, TEXT_NOT_PROVEN, "info")
    if outcome == "rueckfrage" and statement_choices(result):
        return OutcomeView("state", TITLE_STATEMENT, TEXT_STATEMENT, "info")
    if outcome == "rueckfrage":
        return OutcomeView("state", TITLE_QUESTION, TEXT_QUESTION, "info")
    if outcome == "quelle_fehlt":
        if asks_to_pick_source(result):
            return OutcomeView("state", TITLE_PICK_SOURCE, TEXT_PICK_SOURCE, "warn")
        return OutcomeView("state", TITLE_NO_SOURCE, TEXT_NO_SOURCE, "warn")
    return OutcomeView("state", TITLE_TECHNICAL, _flat(result.error) if result.error else TEXT_TECHNICAL, "error")


# --- card and source selection -------------------------------------------------------------------------------------
def prediction_for_card(result: "AgentResult") -> PredictionResult:
    """The engine's prediction as the result card needs it: a copy whose label follows the result's outcome (also
    `nicht_belegt` after a discarded verdict) and whose quotes and reasoning are the checked ones. The result's own
    prediction stays as it is. ValueError for a result without a verdict or without the engine's prediction."""
    label = OUTCOME_LABELS.get(result.outcome)
    if label is None or result.prediction is None:
        raise ValueError(f"Zum Ergebnis «{result.outcome}» gibt es keine Karte.")
    update = {
        "label": label,
        "label_name": config.LABEL_MAPPING[label],
        "evidence": [item.quote for item in result.evidence],
        "evidence_sources": [EvidenceSource(quote=item.quote, page_number=item.page) for item in result.evidence],
    }
    if result.reasoning:  # a discarded verdict must not keep the engine's reasoning for the dropped label
        update["reasoning"] = result.reasoning
    return result.prediction.model_copy(update=update)


def vote_for_selection(votes: Mapping[str, "VoteRef"], source_id: str, title: Optional[str], *,
                       booklets_dir: Path, catalog: Optional[Mapping[str, Mapping[str, Any]]] = None) -> "VoteRef":
    """The vote that the app's source selection (a booklet and optionally one vote title) means: the booklet's vote
    with that title, otherwise its whole-booklet vote (`#0`) from `votes`. Without a title ("Automatisch erkennen")
    and without a `#0` in `votes` it is a new whole-booklet vote of that booklet (`uploads.whole_booklet_vote`, all
    pages of its PDF in `booklets_dir`), which the check's backend has to register (`LocalBackend.add_vote`); the
    only function of this module that reads a file.

    A booklet of `catalog` (file name -> catalog entry, as `available_sources` returns it) that `votes` does not
    know at all, a French or Italian booklet of the German-only index, is that whole-booklet vote too, with the
    entry's date and language, also with a title that is one of the entry's votes (the engine then finds the vote
    inside the booklet). ValueError if the booklet is in neither, a title names none of its votes or its PDF cannot
    be read."""
    if title is not None:
        for vote in votes.values():
            if vote.source_id == source_id and vote.title == title:
                return vote
    whole = votes.get(f"{source_id}#0")
    if whole is not None:
        return whole
    known = next((vote for vote in votes.values() if vote.source_id == source_id), None)
    entry = (catalog or {}).get(f"{source_id}.pdf") if known is None else None
    if entry is not None and (title is None or title in entry.get("votes", ())):
        known = _catalog_ref(source_id, entry)
    elif title is not None or known is None:
        raise ValueError(f"Für die Quelle {source_id} gibt es keine passende Vorlage.")
    from src.agent.uploads import whole_booklet_vote  # reads the PDF (PyMuPDF), only when it is needed
    return whole_booklet_vote(booklets_dir, known)


def _catalog_ref(source_id: str, entry: Mapping[str, Any]) -> "VoteRef":
    """A stand-in for an index vote of a catalog booklet: what `whole_booklet_vote` needs (source, date, language)."""
    from src.agent.source_index import VoteRef
    return VoteRef(f"{source_id}#0", source_id, entry["date"], entry["language"], TITLE_WHOLE_BOOKLET, ())


# --- answer buttons ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class OptionButton:
    vote: "VoteRef"
    label: str  # the vote's title; the app shows it with `TEXT_LAST_VOTE` when `is_last`
    is_last: bool  # the person's last chosen vote: the "wie zuletzt" button, an extra one or an option's own


def options_with_last(options: Sequence["VoteRef"], last: Optional["VoteRef"]) -> List[OptionButton]:
    """One button per option in the order given, and the "wie zuletzt" button for `last`: the option's own button
    carries the mark if `last` is one of the options (no duplicate), otherwise one extra button comes after the
    options. Without `last` the plain options. The buttons only offer; nothing is chosen until the person clicks."""
    buttons = [OptionButton(vote, vote.title, last is not None and vote.vote_id == last.vote_id) for vote in options]
    if last is not None and not any(button.is_last for button in buttons):
        buttons.append(OptionButton(last, last.title, True))
    return buttons


# --- live stream ---------------------------------------------------------------------------------------------------
_DONE = object()  # the worker's last queue item: the call has ended
POLL_SECONDS = 1.0  # only how often a worker that died without its marker is noticed; events wake the reader at once


def stream(call: Callable[[], "Union[Pending, AgentResult]"],
           events: "queue.Queue[TraceEvent]") -> Iterator[Union[TraceEvent, "Pending", "AgentResult"]]:
    """Runs `call` in a worker thread and yields every event it puts into `events` as soon as it arrives; the last
    item is the call's return value (the turn). An exception of `call` is raised here after the events queued before
    it were yielded. `events` is the queue the tracer's `on_event` fills; nothing else may put into it. The worker is a
    daemon and joined once the call has ended; a consumer that stops early leaves it to finish by itself."""
    outcome: dict = {}

    def work() -> None:
        try:
            outcome["turn"] = call()
        except BaseException as exc:  # handed to the caller, never lost in the thread
            outcome["error"] = exc
        finally:
            events.put(_DONE)

    worker = threading.Thread(target=work, name="agent-check", daemon=True)
    worker.start()
    while True:
        try:
            item = events.get(timeout=POLL_SECONDS)
        except queue.Empty:
            if worker.is_alive():
                continue
            break
        if item is _DONE:
            break
        yield item
    worker.join()
    if "error" in outcome:
        raise outcome["error"]
    yield outcome["turn"]
