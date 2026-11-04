"""The agent's LangGraph: search loop, fixed verification, deterministic evidence check, result.

    START → decide → act ─┬─→ decide (search result or rejected action fed back)
                          ├─→ verify → check_evidence ─┬─→ finish → END
                          │                            └─→ nachsuche → decide (one search step) → act
                          │                                  └─→ verify (new passages, once) → check_evidence → finish
                          ├─→ ask → finish (rueckfrage, from the guard or from the model)
                          └─→ finish (quelle_fehlt, technical error)

* `decide` asks the decider for the next action; every answer is one step (`steps`).
* `act` runs a search tool, or accepts a terminal action (`pruefen`, `rueckfrage`, `quelle_fehlt`) after validating
  its arguments and resolving every vote id with `AgentTools.check_vote_id`. An invalid action, invalid arguments or a
  rejected terminal action is fed back to the decider as an observation and is a search step. A failed tool ends the
  run as `technischer_fehler`.
* The guard sits in `act`: without a fixed vote, before the first vote-bound action (`suche_passagen`,
  `suche_passagen_vektor`, `pruefen`, with valid arguments and a known vote id) it assesses the vote ranking of the
  claim's own wording (`AgentTools.claim_assessment`, never the model's query). If the claim fits several votes
  ("mehrdeutig") the action is not run and the route goes to `ask` with the close votes; a failure of that search is a
  technical error, never `nicht_belegt`.
* `ask` is the only node that sets the outcome `rueckfrage` (`asked_by` says whether the guard or the model asked).
  Stage 4 replaces the ending here with `interrupt` and continues the check with the chosen vote.
* `quelle_fehlt` is accepted only after a search: without a fixed vote after a `suche_vorlagen` without error, with
  a fixed vote after a passage search. Otherwise it is rejected like any invalid action.
* At most `max_steps` search steps. After them the decider is asked once more and offered only the terminal tools; if
  that answer does not end the search, the run ends as `technischer_fehler` ("Schrittgrenze").
* `verify` checks the claim against the vote's pooled passages with the proven NLI path. `PredictionResult.error`
  is a technical error, whatever the label.
* `nachsuche` (phase "nachsuche"): if the first check ends as `nicht_belegt` (a discarded verdict too), the run gets
  exactly one more search step before the second and final check (`settings.MAX_CHECKS`, `MAX_RESEARCH_STEPS`; off
  with `nachsuche=False`). `decide` offers only `suche_passagen_vektor` and `act` accepts only that tool for the checked
  vote; the step limit `max_steps` does not apply in this phase, a run that spent all its search steps still gets
  its Nachsuche. Anything else (invalid action, other vote, failed tool, spent budget) is a technical error, never
  `nicht_belegt`. Without new passages (`AgentTools.recheck_passages` is empty) the run ends with the first result
  and `NO_NEW_PASSAGES_NOTE`, without a second `verify`; otherwise the second check runs with the new passages
  first and its result is final.
* `check_evidence` keeps a quote only if its page is one of `vote.pages` and `official_normalize(quote)` is part of the
  normalized PDF text of that page or of one of its PDF text blocks (`engine.source_page`; the engine quotes whole
  pages and blocks). A quote of a page outside the vote counts like a quote that is not found. A verdict
  without a valid quote becomes `nicht_belegt` with the note `DISCARDED_NOTE`. A cited page without readable PDF text
  cannot be checked: technical error, never `nicht_belegt`.
* `run_check` refuses `EVIDENCE_POLICY=legacy` before any LLM call: its quotes are not original PDF text.

Every node emits a `TraceEvent`; an `act` event with a tool result carries its `Observation.data` (vote candidates,
passages and pool status, error kind). Any exception in a node ends the run as `technischer_fehler` with a message.
Sync only (`invoke`): the request budget counts only synchronous requests.
"""
from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from src import config
from src.agent import guard, settings
from src.agent.decider import AgentAction, Decider
from src.agent.prompts import system_prompt
from src.agent.source_index import VoteRef
from src.agent.tools import (ERROR_INVALID_ARGS, TERMINAL_TOOLS, AgentTools, Observation, engine_vote,
                             validate_args)
from src.agent.trace import TraceEvent, Tracer
from src.inference import PredictionResult
from src.text_utils import guess_language, official_normalize

Outcome = Literal["gestuetzt", "widersprochen", "nicht_belegt", "rueckfrage", "quelle_fehlt", "technischer_fehler"]
LABEL_OUTCOMES: Dict[int, str] = {0: "gestuetzt", 1: "nicht_belegt", 2: "widersprochen"}
DISCARDED_NOTE = "Urteil ohne prüfbaren Beleg verworfen"
QUESTION_REASON = "Mehrere Vorlagen passen zur Behauptung, und sie lässt nicht erkennen, welche gemeint ist."
DISCARDED_REASON = ("Die Prüfung ergab «{verdict}», aber kein Zitat steht so im PDF-Text der angegebenen Seite. "
                    "Ohne prüfbaren Beleg gilt die Behauptung als im Dokument nicht belegt.")
TECHNICAL_REASON = "Die Prüfung wurde wegen eines technischen Fehlers abgebrochen; es gibt kein Urteil."
LANGUAGE_REASON = ("Die Behauptung ist nicht auf Deutsch (Sprache: {language}). Die Suche über alle Vorlagen gibt es "
                   "bisher nur für deutsche Büchlein. Bitte die Quelle selbst wählen; mit vorgegebener Quelle wird "
                   "die Behauptung in jeder Sprache geprüft.")
NO_RESULT = "Der Lauf endete ohne Ergebnis."
NACHSUCHE_PROMPT = ("Ergebnis der Prüfung: im Dokument nicht belegt, kein Beleg gefunden. Nachsuche: Du hast genau "
                    "einen weiteren Suchschritt. Rufe suche_passagen_vektor für die Vorlage {vote_id} mit einer anders "
                    "formulierten Anfrage auf. Danach wird ein letztes Mal geprüft.")
NO_NEW_PASSAGES_NOTE = "Nachsuche ohne neue Passagen; es bleibt beim Ergebnis der ersten Prüfung"
NACHSUCHE_ERROR = "Nachsuche ohne gültigen Suchschritt: {reason}"
UNREADABLE_ERROR = ("Belegprüfung nicht möglich: Für {pages} von {source} gibt es keinen lesbaren PDF-Text; die "
                    "Zitate dort lassen sich nicht prüfen.")
LEGACY_POLICY_ERROR = ("EVIDENCE_POLICY=legacy wird vom Agenten nicht unterstützt: Seine Belegprüfung braucht Zitate "
                       "aus dem Originaltext der PDF-Seiten (EVIDENCE_POLICY raw_pages oder raw_pages_and_blocks). Es "
                       "wurde nichts geprüft.")
NO_SEARCH_YET = "quelle_fehlt erst nach einer Suche: Rufe zuerst suche_vorlagen auf."
NO_PASSAGE_SEARCH_YET = ("quelle_fehlt erst nach einer Suche: Suche zuerst mit suche_passagen oder "
                         "suche_passagen_vektor.")
GUARD_ERROR = "Wächter: Vorlagensuche zur Behauptung fehlgeschlagen: {error}"
VOTE_BOUND_TOOLS = frozenset({"suche_passagen", "suche_passagen_vektor", "pruefen"})  # the guard checks before these
PASSAGE_SEARCH_TOOLS = frozenset({"suche_passagen", "suche_passagen_vektor"})
RESEARCH_TOOL = "suche_passagen_vektor"  # the only tool of the Nachsuche
SUMMARY_CHARS = 200
ERROR_CHARS = 300
_PAGE_PREFIX = re.compile(r"^\[(?:page|seite)\s+\d+\] ?", re.IGNORECASE)  # engine prefix, as in to_official_dict


@dataclass
class EvidenceItem:
    page: int
    quote: str  # original PDF text, as the engine quoted it


@dataclass
class AgentResult:
    outcome: Outcome
    vote: Optional[VoteRef] = None
    evidence: List[EvidenceItem] = field(default_factory=list)
    question: Optional[str] = None
    options: List[VoteRef] = field(default_factory=list)
    reasoning: str = ""
    note: Optional[str] = None
    error: Optional[str] = None
    prediction: Optional[PredictionResult] = None
    events: List[TraceEvent] = field(default_factory=list)
    steps: int = 0
    asked_by: Optional[Literal["guard", "model"]] = None  # who asked back; only with outcome "rueckfrage"
    checks: int = 0  # evidence checks that ran (verify calls): 0, 1 or 2


class _State(TypedDict, total=False):
    claim: str
    language: str
    messages: List[dict]  # [system, user = claim, then the exchanges the decider writes]
    history: List[Observation]  # observations fed back so far, rejections included
    steps: int  # decisions so far
    search_steps: int  # decisions that did not end the search
    vote_searches: int  # suche_vorlagen runs without error
    passage_searches: int  # suche_passagen / suche_passagen_vektor runs without error
    phase: str  # "nachsuche" while the single research step runs
    checks: int  # evidence checks run so far (verify calls)
    checked: List[Dict[str, Any]]  # the passages of the first check
    first: dict  # the first check's outcome, reasoning, note and evidence, kept for a Nachsuche without new passages
    guard_done: bool  # the guard has assessed the claim and let the first vote-bound action through
    asked_by: str  # "guard" or "model"
    ask_data: dict  # what the ask event carries: the guard's assessment or the model's options
    offered: List[str]  # tools offered for the latest decision
    action: AgentAction  # the latest decision
    route: str  # next node
    vote: VoteRef
    passages: List[Dict[str, Any]]
    prediction: PredictionResult
    outcome: str
    evidence: List[EvidenceItem]
    question: str
    options: List[VoteRef]
    reasoning: str
    note: str
    error: str


def _short(text: Any, limit: int = SUMMARY_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:ERROR_CHARS]}"


def page_texts(engine, pdf, page: int) -> List[str]:
    """The normalized PDF text of the page and of each of its text blocks; empty: the page has no readable text.

    The forms a quote may be found in: this is the evidence check's rule, shared with the testset validator.
    """
    source = engine.source_page(pdf, page) or {}
    texts = [source.get("text") or ""] + list(source.get("blocks") or [])
    return [text for text in (official_normalize(t) for t in texts if isinstance(t, str)) if text]


class _Nodes:
    def __init__(self, tools: AgentTools, decider: Decider, engine, tracer: Tracer, max_steps: int, budget: Any,
                 nachsuche: bool = True):
        self.tools = tools
        self.decider = decider
        self.engine = engine
        self.tracer = tracer
        self.max_steps = max_steps
        self.budget = budget
        self.nachsuche_enabled = nachsuche

    # --- helpers -------------------------------------------------------------------------------------------------

    def emit(self, step: int, node: str, started: float, **fields) -> None:
        self.tracer.emit(step=step, node=node, duration_ms=(time.perf_counter() - started) * 1000, **fields)

    def guarded(self, name: str, node: Callable[[_State], dict]) -> Callable[[_State], dict]:
        """Any exception of `node` ends the run as a technical error instead of propagating out of the graph."""
        def run(state: _State) -> dict:
            started = time.perf_counter()
            try:
                return node(state)
            except Exception as exc:
                message = f"Ausnahme im Knoten {name}: {_exception(exc)}"
                try:
                    self.emit(state.get("steps", 0), name, started, summary="Ausnahme", error=message)
                except Exception:
                    pass  # the trace itself failed; the message still reaches the result
                return {"error": message, "route": "finish"}
        return run

    def booklet_pdf(self, vote: VoteRef):
        return self.tools.booklets_dir / f"{vote.source_id}.pdf"

    def terminal_names(self) -> List[str]:
        return [spec["name"] for spec in self.tools.available_specs() if spec["name"] in TERMINAL_TOOLS]

    # --- nodes ---------------------------------------------------------------------------------------------------

    def decide(self, state: _State) -> dict:
        step = state.get("steps", 0) + 1
        specs = self.tools.available_specs()
        researching = state.get("phase") == "nachsuche"  # one search step, governed by MAX_RESEARCH_STEPS
        last = not researching and state.get("search_steps", 0) >= self.max_steps  # it must end the search
        if researching:
            specs = [spec for spec in specs if spec["name"] == RESEARCH_TOOL]
        elif last:
            specs = [spec for spec in specs if spec["name"] in TERMINAL_TOOLS]
        offered = [spec["name"] for spec in specs]
        started = time.perf_counter()
        try:
            action = self.decider.decide(list(state["messages"]), specs, list(state.get("history", [])))
        except Exception as exc:  # timeout, HTTP error, spent budget: technical, never a verdict
            message = f"Entscheider fehlgeschlagen: {_exception(exc)}"
            self.emit(step, "decide", started, summary="Keine Entscheidung", error=message)
            return {"steps": step, "error": message, "route": "finish"}
        summary = (f"{action.tool} gewählt" if action.error is None
                   else f"Keine gültige Aktion: {_short(action.error)}")
        if last:
            summary += " (nach der Schrittgrenze, nur abschließende Werkzeuge angeboten)"
        self.emit(step, "decide", started, tool=action.tool, args=dict(action.args or {}), summary=summary,
                  tokens_prompt=action.tokens_prompt, tokens_completion=action.tokens_completion, error=action.error)
        return {"steps": step, "offered": offered, "action": action, "route": "act"}

    def act(self, state: _State) -> dict:
        action, step = state["action"], state["steps"]
        tool = action.tool if isinstance(action.tool, str) else None
        args = dict(action.args or {})
        history = list(state.get("history", []))
        started = time.perf_counter()
        if state.get("phase") == "nachsuche":
            return self._act_nachsuche(state, started)
        rejection = self._precheck(action, state.get("offered", []))
        guard_data: Optional[dict] = None
        guard_done = state.get("guard_done", False)
        if rejection is None and not guard_done and self._guard_applies(action):
            try:
                assessment = self.tools.claim_assessment(state["claim"])  # the claim's wording, not the model's query
            except Exception as exc:  # a guard that cannot look is a technical error, never "nicht belegt"
                message = GUARD_ERROR.format(error=_exception(exc))
                self.emit(step, "act", started, tool=tool, args=args, summary="Wächter: Vorlagensuche fehlgeschlagen",
                          error=message)
                return {"error": message, "route": "finish"}
            guard_data, guard_done = assessment.to_data(), True
            if assessment.kind == "mehrdeutig":  # several votes fit: the action is not run
                options = [self.tools.check_vote_id(vote_id) for vote_id in assessment.close]
                self.emit(step, "act", started, tool=tool, args=args, data={"guard": guard_data},
                          summary=f"Wächter: mehrdeutig, {', '.join(assessment.close)} liegen gleichauf; "
                                  f"{tool} nicht ausgeführt")
                return {"guard_done": True, "asked_by": "guard", "question": guard.question(options),
                        "options": options, "ask_data": guard_data, "route": "ask"}
        guard_event = {"guard": guard_data} if guard_data is not None else {}
        if rejection is None and tool in TERMINAL_TOOLS:
            try:
                update, summary = self._accept_terminal(state, action)
            except ValueError as exc:  # invalid arguments, unknown vote id, no passages, no search before quelle_fehlt
                rejection = str(exc)
            else:
                self.emit(step, "act", started, tool=tool, args=args, summary=summary, data=guard_event)
                return {**update, "guard_done": guard_done}
        if state.get("search_steps", 0) >= self.max_steps:  # the last decision did not end the search
            reason = (rejection or f"{tool} beendet die Suche nicht").rstrip(" .")
            message = (f"Schrittgrenze erreicht: Nach {self.max_steps} Suchschritten wurde die Suche nicht beendet "
                       f"({_short(reason)}).")
            self.emit(step, "act", started, tool=tool, args=args, summary="Schrittgrenze", error=message)
            return {"error": message, "route": "finish"}
        if rejection is not None:
            observation = Observation(tool or "Werkzeugwahl", f"Fehler: {rejection}",
                                      {"error_kind": ERROR_INVALID_ARGS}, rejection)
        else:
            observation = self.tools.run(tool, action.args, claim=state["claim"])
            if observation.error and observation.data.get("error_kind") != ERROR_INVALID_ARGS:  # a tool failure
                message = f"Werkzeug {tool} fehlgeschlagen: {observation.error}"
                self.emit(step, "act", started, tool=tool, args=args, summary=_short(observation.text),
                          error=message, data={**observation.data, **guard_event})
                return {"history": history + [observation], "error": message, "route": "finish"}
        search_steps = state.get("search_steps", 0) + 1
        searches = {}
        if observation.error is None:  # a rejected or failed search is no search
            if tool == "suche_vorlagen":
                searches["vote_searches"] = state.get("vote_searches", 0) + 1
            elif tool in PASSAGE_SEARCH_TOOLS:
                searches["passage_searches"] = state.get("passage_searches", 0) + 1
        shown = observation
        if search_steps >= self.max_steps:
            hint = (f"Hinweis: Die Schrittgrenze von {self.max_steps} Suchschritten ist erreicht. Beende die Suche "
                    f"jetzt mit {', '.join(self.terminal_names())}.")
            shown = Observation(observation.tool, f"{observation.text}\n\n{hint}", observation.data, observation.error)
        messages = list(state["messages"]) + self.decider.observation_messages(action, shown)
        self.emit(step, "act", started, tool=tool, args=args,
                  summary=_short(observation.text.splitlines()[0] if observation.text else ""),
                  error=observation.error, data={**observation.data, **guard_event})  # what the model saw
        return {"messages": messages, "history": history + [observation], "search_steps": search_steps,
                "guard_done": guard_done, "route": "decide", **searches}

    def _act_nachsuche(self, state: _State, started: float) -> dict:
        """The single research step: a valid `suche_passagen_vektor` in the checked vote, else a technical error."""
        action, step, vote = state["action"], state["steps"], state["vote"]
        tool = action.tool if isinstance(action.tool, str) else None
        args = dict(action.args or {})
        history = list(state.get("history", []))
        problem: Optional[str] = self._precheck(action, state.get("offered", []))
        if problem is None:
            try:
                clean = validate_args(RESEARCH_TOOL, action.args)
            except ValueError as exc:
                problem = str(exc)
            else:
                if clean["vorlage_id"] != vote.vote_id:
                    problem = (f"Die Nachsuche gilt der geprüften Vorlage {vote.vote_id}, nicht "
                               f"{clean['vorlage_id']!r}.")
        observation = None
        if problem is None:
            observation = self.tools.run(RESEARCH_TOOL, action.args, claim=state["claim"])
            if observation.error:
                problem = f"Werkzeug {RESEARCH_TOOL} fehlgeschlagen: {observation.error}"
        if problem is not None:
            message = NACHSUCHE_ERROR.format(reason=problem)
            data = observation.data if observation is not None else None
            self.emit(step, "act", started, tool=tool, args=args, summary="Nachsuche ohne gültigen Suchschritt",
                      error=message, data=data)
            update: dict = {"error": message, "route": "finish"}
            if observation is not None:
                update["history"] = history + [observation]
            return update
        passages = self.tools.recheck_passages(vote.vote_id, state["checked"])
        first = state["first"]
        update = {"history": history + [observation], "passage_searches": state.get("passage_searches", 0) + 1}
        summary = _short(observation.text.splitlines()[0] if observation.text else "")
        if not passages:  # nothing new to check: the first result stands
            self.emit(step, "act", started, tool=tool, args=args, data=observation.data,
                      summary=f"{summary} Keine neuen Passagen, keine zweite Prüfung")
            note = f"{first['note']}. {NO_NEW_PASSAGES_NOTE}" if first.get("note") else NO_NEW_PASSAGES_NOTE
            return {**update, "outcome": first["outcome"], "evidence": first["evidence"],
                    "reasoning": first["reasoning"], "note": note, "route": "finish"}
        self.emit(step, "act", started, tool=tool, args=args, data=observation.data,
                  summary=f"{summary} Zweite Prüfung mit {len(passages)} Passagen")
        return {**update, "passages": passages, "route": "verify"}

    def _guard_applies(self, action: AgentAction) -> bool:
        """True if the guard checks before this action: no fixed vote, a vote-bound tool with valid arguments and a
        known vote id. Anything else is rejected as before and needs no guard."""
        if self.tools.fixed_vote_id is not None or action.tool not in VOTE_BOUND_TOOLS:
            return False
        try:
            self.tools.check_vote_id(validate_args(action.tool, action.args)["vorlage_id"])
        except ValueError:
            return False
        return True

    @staticmethod
    def _precheck(action: AgentAction, offered: List[str]) -> Optional[str]:
        """Why the decision is no executable action, or None."""
        if action.error is not None or action.tool is None:
            return action.error or "Keine Werkzeugwahl."
        if action.tool not in offered:
            return f"{action.tool} ist in dieser Prüfung nicht verfügbar. Verfügbar: {', '.join(offered)}."
        return None

    def _accept_terminal(self, state: _State, action: AgentAction) -> Tuple[dict, str]:
        """The state update and trace summary of a valid terminal action; ValueError (German) otherwise."""
        args = validate_args(action.tool, action.args)
        if action.tool == "pruefen":
            vote = self.tools.check_vote_id(args["vorlage_id"])
            passages = self.tools.passages_for(vote.vote_id)
            if not passages:
                raise ValueError(f"Für die Vorlage {vote.vote_id} gibt es noch keine Passagen im Pool. Suche zuerst "
                                 "mit suche_passagen oder suche_passagen_vektor.")
            return ({"vote": vote, "passages": passages, "route": "verify"},
                    f"Prüfung mit {len(passages)} Passagen aus {vote.vote_id}")
        if action.tool == "rueckfrage":
            options = [self.tools.check_vote_id(vote_id) for vote_id in args["optionen"]]
            return ({"question": args["frage"], "options": options, "asked_by": "model",
                     "ask_data": {"options": [{"vote_id": o.vote_id, "title": o.title, "date": o.date}
                                              for o in options]}, "route": "ask"},
                    f"Rückfrage mit {len(options)} Optionen: {', '.join(o.vote_id for o in options)}")
        if self.tools.fixed_vote_id is None and not state.get("vote_searches", 0):
            raise ValueError(NO_SEARCH_YET)
        if self.tools.fixed_vote_id is not None and not state.get("passage_searches", 0):
            raise ValueError(NO_PASSAGE_SEARCH_YET)
        return ({"outcome": "quelle_fehlt", "reasoning": args["begruendung"], "route": "finish"},
                f"Quelle fehlt: {_short(args['begruendung'])}")

    def ask(self, state: _State) -> dict:
        """The only node that sets the outcome `rueckfrage`. Stage 4 replaces ending here with `interrupt`."""
        options, step = state["options"], state["steps"]
        asked_by = state.get("asked_by", "model")
        started = time.perf_counter()
        ids = [option.vote_id for option in options]
        self.emit(step, "ask", started, tool="rueckfrage", args={"optionen": ids}, data=state.get("ask_data"),
                  summary=f"Rückfrage ({'Wächter' if asked_by == 'guard' else 'Modell'}) mit {len(ids)} Optionen: "
                          f"{', '.join(ids)}")
        return {"outcome": "rueckfrage", "reasoning": QUESTION_REASON, "route": "finish"}

    def verify(self, state: _State) -> dict:
        vote, passages, step = state["vote"], state["passages"], state["steps"]
        args = {"vorlage_id": vote.vote_id, "passagen": len(passages)}
        checks = state.get("checks", 0) + 1
        started = time.perf_counter()
        prediction = self.engine.verify_with_passages(state["claim"], self.booklet_pdf(vote), passages,
                                                      vote=engine_vote(vote), claim_language=state.get("language"))
        tokens = {"tokens_prompt": prediction.tokens_prompt, "tokens_completion": prediction.tokens_completion}
        message = None
        if prediction.error:  # the engine's neutral fallback: technical, never "nicht belegt"
            message = f"Prüfung fehlgeschlagen: {_short(prediction.error, ERROR_CHARS)}"
        elif prediction.label not in LABEL_OUTCOMES:
            message = f"Prüfung lieferte das unbekannte Label {prediction.label!r}."
        if message is not None:
            self.emit(step, "verify", started, tool="pruefen", args=args, summary="Kein Urteil", error=message,
                      **tokens)
            return {"prediction": prediction, "checks": checks, "error": message, "route": "finish"}
        summary = (f"Label {prediction.label} ({LABEL_OUTCOMES[prediction.label]}), "
                   f"{len(prediction.evidence_sources)} Zitate")
        self.emit(step, "verify", started, tool="pruefen", args=args, summary=summary, **tokens)
        return {"prediction": prediction, "checks": checks, "route": "check_evidence"}

    def check_evidence(self, state: _State) -> dict:
        prediction, vote, step = state["prediction"], state["vote"], state["steps"]
        verdict = LABEL_OUTCOMES[prediction.label]
        started = time.perf_counter()
        if verdict == "nicht_belegt":
            self.emit(step, "check_evidence", started, summary="Nicht belegt: kein Zitat zu prüfen")
            return {"outcome": verdict, "evidence": [], "reasoning": prediction.reasoning, "note": None,
                    "route": self._after_not_proven(state)}
        evidence, unreadable = self._valid_evidence(vote, prediction)
        total = len(prediction.evidence_sources)
        if unreadable:  # the check could not be done: technical, never "nicht belegt"
            pages = ("Seite " if len(unreadable) == 1 else "Seiten ") + ", ".join(map(str, unreadable))
            message = UNREADABLE_ERROR.format(pages=pages, source=f"{vote.source_id}.pdf")
            self.emit(step, "check_evidence", started, summary="Belegprüfung nicht möglich", error=message)
            return {"error": message, "route": "finish"}
        if evidence:
            self.emit(step, "check_evidence", started,
                      summary=f"{len(evidence)} gültige Belege aus {total} Zitaten: {verdict}")
            return {"outcome": verdict, "evidence": evidence, "reasoning": prediction.reasoning, "note": None,
                    "route": "finish"}
        self.emit(step, "check_evidence", started,
                  summary=f"Urteil «{verdict}» verworfen: keines von {total} Zitaten steht im PDF-Text seiner Seite")
        return {"outcome": "nicht_belegt", "evidence": [], "note": DISCARDED_NOTE,
                "reasoning": DISCARDED_REASON.format(verdict=verdict), "route": self._after_not_proven(state)}

    def _after_not_proven(self, state: _State) -> str:
        """After `nicht_belegt` (a discarded verdict too) the run searches once more, while a check is left."""
        return "nachsuche" if self.nachsuche_enabled and state.get("checks", 0) < settings.MAX_CHECKS else "finish"

    def nachsuche(self, state: _State) -> dict:
        """Starts the research phase: remembers the first check and offers one more search step (`decide` and `act`
        know the phase)."""
        vote, step = state["vote"], state["steps"]
        started = time.perf_counter()
        self.emit(step, "nachsuche", started,
                  summary=f"Nachsuche: nicht belegt, ein weiterer Suchschritt in {vote.vote_id}")
        first = {"outcome": state["outcome"], "evidence": list(state.get("evidence", [])),
                 "reasoning": state.get("reasoning", ""), "note": state.get("note")}
        prompt = {"role": "user", "content": NACHSUCHE_PROMPT.format(vote_id=vote.vote_id)}
        return {"phase": "nachsuche", "checked": list(state["passages"]), "first": first,
                "messages": list(state["messages"]) + [prompt], "route": "decide"}

    def _page_texts(self, pdf, page: int) -> List[str]:
        return page_texts(self.engine, pdf, page)

    def _valid_evidence(self, vote: VoteRef, prediction: PredictionResult) -> Tuple[List[EvidenceItem], List[int]]:
        """The quotes found verbatim (after `official_normalize`) in the PDF text of their page or of one of its text
        blocks, without duplicates, on a page of `vote.pages` only; and the cited pages without readable PDF text,
        whose quotes cannot be checked."""
        pdf = self.booklet_pdf(vote)
        pages: Dict[int, List[str]] = {}
        seen, items, unreadable = set(), [], []
        for source in prediction.evidence_sources:
            page = source.page_number
            if type(page) is not int or page < 1:
                continue
            quote = _PAGE_PREFIX.sub("", source.quote)
            normalized = official_normalize(quote)
            if not normalized or (page, normalized) in seen:
                continue
            if page not in vote.pages:  # a quote of another vote's page is no evidence for this vote
                continue
            if page not in pages:
                pages[page] = self._page_texts(pdf, page)
                if not pages[page]:
                    unreadable.append(page)
            if any(normalized in text for text in pages[page]):
                seen.add((page, normalized))
                items.append(EvidenceItem(page=page, quote=quote))
        return items, sorted(unreadable)

    def finish(self, state: _State) -> dict:
        outcome, error = state.get("outcome"), state.get("error")
        if error is None and self.budget is not None and getattr(self.budget, "exceeded", False):
            # a refused request may have been swallowed (e.g. the query translation): the result is incomplete
            error = "Anfrage-Budget erschöpft: Mindestens eine Anfrage wurde nicht gesendet."
        if error is None and outcome is None:
            error = NO_RESULT
        if error is not None:
            outcome = "technischer_fehler"
        summary = f"Ergebnis: {outcome}"
        if outcome == "rueckfrage":
            summary += f", {len(state.get('options', []))} Optionen"
        elif outcome in ("gestuetzt", "widersprochen"):
            summary += f", {len(state.get('evidence', []))} Belege"
        self.emit(state.get("steps", 0), "finish", time.perf_counter(), summary=summary, error=error)
        return {"outcome": outcome, "error": error}


def _route(state: _State) -> str:
    return state["route"]


def build_graph(tools: AgentTools, decider: Decider, engine, tracer: Tracer, max_steps: int = settings.MAX_STEPS,
                *, budget: Any = None, nachsuche: bool = True):
    """The compiled graph. Its input state needs `claim`, `language` and `messages`; see `run_check`.

    `nachsuche=False` switches the research loop off: the first `nicht_belegt` ends the run (evaluation arm B2).

    `budget` (a `RequestBudget`, optional): if a request was refused during the run, the result is a technical error
    even when the refusal was swallowed on the way (the engine's query translation returns no translation).
    """
    if max_steps < 1:
        raise ValueError(f"max_steps must be at least 1, got {max_steps!r}")
    nodes = _Nodes(tools, decider, engine, tracer, max_steps, budget, nachsuche)
    graph = StateGraph(_State)
    for name in ("decide", "act", "ask", "verify", "check_evidence", "nachsuche", "finish"):
        graph.add_node(name, nodes.guarded(name, getattr(nodes, name)))
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", _route, {"act": "act", "finish": "finish"})
    graph.add_conditional_edges("act", _route, {"decide": "decide", "ask": "ask", "verify": "verify",
                                                "finish": "finish"})
    graph.add_edge("ask", "finish")
    graph.add_conditional_edges("verify", _route, {"check_evidence": "check_evidence", "finish": "finish"})
    graph.add_conditional_edges("check_evidence", _route, {"nachsuche": "nachsuche", "finish": "finish"})
    graph.add_edge("nachsuche", "decide")
    graph.add_edge("finish", END)
    # decide + act for each of at most max_steps + 2 decisions (the last search decision and the Nachsuche), then
    # verify, check_evidence, nachsuche, verify, check_evidence, finish, plus 3 spare
    return graph.compile().with_config(recursion_limit=2 * (max_steps + 2) + 9)


def _result(state: dict, events: List[TraceEvent]) -> AgentResult:
    common = {"vote": state.get("vote"), "prediction": state.get("prediction"), "events": events,
              "steps": state.get("steps", 0), "checks": state.get("checks", 0)}
    if state.get("error") is not None or state.get("outcome") in (None, "technischer_fehler"):
        return AgentResult(outcome="technischer_fehler", reasoning=TECHNICAL_REASON,
                           error=state.get("error") or NO_RESULT, **common)
    return AgentResult(outcome=state["outcome"], evidence=list(state.get("evidence", [])),
                       question=state.get("question"), options=list(state.get("options", [])),
                       reasoning=state.get("reasoning", ""), note=state.get("note"),
                       asked_by=state.get("asked_by") if state["outcome"] == "rueckfrage" else None, **common)


def run_check(claim: str, *, tools: AgentTools, decider: Decider, engine, tracer: Tracer,
              claim_language: Optional[str] = None, budget: Any = None, nachsuche: bool = True) -> AgentResult:
    """One check of `claim`. Raises ValueError only for a claim outside 1-1000 characters; every runtime failure is
    an `AgentResult` with `outcome="technischer_fehler"`.

    Without a given vote (`tools.fixed_vote_id`) only claims in `settings.LANGUAGES` are searched; others end as
    `quelle_fehlt` before any LLM call. `claim_language` defaults to `guess_language(claim)`. With `budget` (the
    check's `RequestBudget`) a refused request makes the result a technical error even if it was swallowed.
    `EVIDENCE_POLICY=legacy` is refused as a technical error before any LLM call. With `nachsuche=False` a first
    `nicht_belegt` is final (no research step, no second check).
    """
    if not isinstance(claim, str) or not claim.strip() or len(claim) > settings.MAX_CLAIM_CHARS:
        length = len(claim) if isinstance(claim, str) else type(claim).__name__
        raise ValueError(f"Die Behauptung muss 1 bis {settings.MAX_CLAIM_CHARS} Zeichen lang sein "
                         f"(erhalten: {length}).")
    first_event = len(tracer.events)
    try:
        if config.EVIDENCE_POLICY == "legacy":  # its quotes are not original PDF text: the evidence check cannot work
            tracer.emit(step=0, node="finish", summary="Ergebnis: technischer_fehler", error=LEGACY_POLICY_ERROR)
            return AgentResult(outcome="technischer_fehler", reasoning=TECHNICAL_REASON, error=LEGACY_POLICY_ERROR,
                               events=tracer.events[first_event:])
        language = (claim_language or guess_language(claim)).strip().lower()
        if tools.fixed_vote_id is None and language not in settings.LANGUAGES:
            reasoning = LANGUAGE_REASON.format(language=language)
            tracer.emit(step=0, node="finish", tool="quelle_fehlt", args={"sprache": language},
                        summary=f"Ergebnis: quelle_fehlt, Sprache {language} ohne vorgegebene Quelle")
            return AgentResult(outcome="quelle_fehlt", reasoning=reasoning, events=tracer.events[first_event:])
        fixed_vote = tools.check_vote_id(tools.fixed_vote_id) if tools.fixed_vote_id is not None else None
        messages = [{"role": "system", "content": system_prompt(fixed_vote)}, {"role": "user", "content": claim}]
        graph = build_graph(tools, decider, engine, tracer, budget=budget, nachsuche=nachsuche)
        state = graph.invoke({"claim": claim, "language": language, "messages": messages, "history": [],
                              "steps": 0, "search_steps": 0})
        return _result(state, tracer.events[first_event:])
    except Exception as exc:  # outside the nodes (graph runtime, trace file): still a result, never a raise
        message = f"Ausnahme im Agentenlauf: {_exception(exc)}"
        try:
            tracer.emit(step=0, node="finish", summary="Ausnahme", error=message)
        except Exception:
            pass
        events = tracer.events[first_event:]
        return AgentResult(outcome="technischer_fehler", reasoning=TECHNICAL_REASON, error=message, events=events,
                           steps=max((e.step for e in events), default=0))
