"""The agent's LangGraph: search loop, fixed verification, deterministic evidence check, result.

    START → decide → act ─┬─→ decide (search result or rejected action fed back)
                          ├─→ verify → check_evidence → finish → END
                          └─→ finish (rueckfrage, quelle_fehlt, technical error)

* `decide` asks the decider for the next action; every answer is one step (`steps`).
* `act` runs a search tool, or accepts a terminal action (`pruefen`, `rueckfrage`, `quelle_fehlt`) after validating
  its arguments and resolving every vote id with `AgentTools.check_vote_id`. An invalid action, invalid arguments or a
  rejected terminal action is fed back to the decider as an observation and is a search step. A failed tool ends the
  run as `technischer_fehler`.
* At most `max_steps` search steps. After them the decider is asked once more and offered only the terminal tools; if
  that answer does not end the search, the run ends as `technischer_fehler` ("Schrittgrenze").
* `verify` checks the claim against the vote's pooled passages with the proven NLI path. `PredictionResult.error`
  is a technical error, whatever the label.
* `check_evidence` keeps a quote only if `official_normalize(quote)` is part of the normalized PDF text of its page. A
  verdict without a valid quote becomes `nicht_belegt` with the note `DISCARDED_NOTE`.

Every node emits a `TraceEvent`; any exception in a node ends the run as `technischer_fehler` with a message.
Sync only (`invoke`): the request budget counts only synchronous requests.
"""
from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent import settings
from src.agent.decider import AgentAction, Decider
from src.agent.prompts import system_prompt
from src.agent.source_index import VoteRef
from src.agent.tools import TERMINAL_TOOLS, AgentTools, Observation, validate_args
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


class _State(TypedDict, total=False):
    claim: str
    language: str
    messages: List[dict]  # [system, user = claim, then the exchanges the decider writes]
    history: List[Observation]  # observations fed back so far, rejections included
    steps: int  # decisions so far
    search_steps: int  # decisions that did not end the search
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


class _Nodes:
    def __init__(self, tools: AgentTools, decider: Decider, engine, tracer: Tracer, max_steps: int, budget: Any):
        self.tools = tools
        self.decider = decider
        self.engine = engine
        self.tracer = tracer
        self.max_steps = max_steps
        self.budget = budget

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
        last = state.get("search_steps", 0) >= self.max_steps  # the last decision: it must end the search
        if last:
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
        rejection = self._precheck(action, state.get("offered", []))
        if rejection is None and tool in TERMINAL_TOOLS:
            try:
                update, summary = self._accept_terminal(action)
            except ValueError as exc:  # invalid arguments, unknown vote id, no passages
                rejection = str(exc)
            else:
                self.emit(step, "act", started, tool=tool, args=args, summary=summary)
                return update
        if state.get("search_steps", 0) >= self.max_steps:  # the last decision did not end the search
            reason = (rejection or f"{tool} beendet die Suche nicht").rstrip(" .")
            message = (f"Schrittgrenze erreicht: Nach {self.max_steps} Suchschritten wurde die Suche nicht beendet "
                       f"({_short(reason)}).")
            self.emit(step, "act", started, tool=tool, args=args, summary="Schrittgrenze", error=message)
            return {"error": message, "route": "finish"}
        if rejection is not None:
            observation = Observation(tool or "Werkzeugwahl", f"Fehler: {rejection}", {"error_kind": "invalid_args"},
                                      rejection)
        else:
            observation = self.tools.run(tool, action.args)
            if observation.error and observation.data.get("error_kind") != "invalid_args":  # tool_failure
                message = f"Werkzeug {tool} fehlgeschlagen: {observation.error}"
                self.emit(step, "act", started, tool=tool, args=args, summary=_short(observation.text),
                          error=message)
                return {"history": history + [observation], "error": message, "route": "finish"}
        search_steps = state.get("search_steps", 0) + 1
        shown = observation
        if search_steps >= self.max_steps:
            hint = (f"Hinweis: Die Schrittgrenze von {self.max_steps} Suchschritten ist erreicht. Beende die Suche "
                    f"jetzt mit {', '.join(self.terminal_names())}.")
            shown = Observation(observation.tool, f"{observation.text}\n\n{hint}", observation.data, observation.error)
        messages = list(state["messages"]) + self.decider.observation_messages(action, shown)
        self.emit(step, "act", started, tool=tool, args=args,
                  summary=_short(observation.text.splitlines()[0] if observation.text else ""),
                  error=observation.error)
        return {"messages": messages, "history": history + [observation], "search_steps": search_steps,
                "route": "decide"}

    @staticmethod
    def _precheck(action: AgentAction, offered: List[str]) -> Optional[str]:
        """Why the decision is no executable action, or None."""
        if action.error is not None or action.tool is None:
            return action.error or "Keine Werkzeugwahl."
        if action.tool not in offered:
            return f"{action.tool} ist in dieser Prüfung nicht verfügbar. Verfügbar: {', '.join(offered)}."
        return None

    def _accept_terminal(self, action: AgentAction) -> Tuple[dict, str]:
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
            return ({"outcome": "rueckfrage", "question": args["frage"], "options": options,
                     "reasoning": QUESTION_REASON, "route": "finish"},
                    f"Rückfrage mit {len(options)} Optionen: {', '.join(o.vote_id for o in options)}")
        return ({"outcome": "quelle_fehlt", "reasoning": args["begruendung"], "route": "finish"},
                f"Quelle fehlt: {_short(args['begruendung'])}")

    def verify(self, state: _State) -> dict:
        vote, passages, step = state["vote"], state["passages"], state["steps"]
        args = {"vorlage_id": vote.vote_id, "passagen": len(passages)}
        started = time.perf_counter()
        prediction = self.engine.verify_with_passages(state["claim"], self.booklet_pdf(vote), passages,
                                                      vote=vote.title, claim_language=state.get("language"))
        tokens = {"tokens_prompt": prediction.tokens_prompt, "tokens_completion": prediction.tokens_completion}
        message = None
        if prediction.error:  # the engine's neutral fallback: technical, never "nicht belegt"
            message = f"Prüfung fehlgeschlagen: {_short(prediction.error, ERROR_CHARS)}"
        elif prediction.label not in LABEL_OUTCOMES:
            message = f"Prüfung lieferte das unbekannte Label {prediction.label!r}."
        if message is not None:
            self.emit(step, "verify", started, tool="pruefen", args=args, summary="Kein Urteil", error=message,
                      **tokens)
            return {"prediction": prediction, "error": message, "route": "finish"}
        summary = (f"Label {prediction.label} ({LABEL_OUTCOMES[prediction.label]}), "
                   f"{len(prediction.evidence_sources)} Zitate")
        self.emit(step, "verify", started, tool="pruefen", args=args, summary=summary, **tokens)
        return {"prediction": prediction, "route": "check_evidence"}

    def check_evidence(self, state: _State) -> dict:
        prediction, vote, step = state["prediction"], state["vote"], state["steps"]
        verdict = LABEL_OUTCOMES[prediction.label]
        started = time.perf_counter()
        if verdict == "nicht_belegt":
            self.emit(step, "check_evidence", started, summary="Nicht belegt: kein Zitat zu prüfen")
            return {"outcome": verdict, "evidence": [], "reasoning": prediction.reasoning, "route": "finish"}
        evidence = self._valid_evidence(vote, prediction)
        total = len(prediction.evidence_sources)
        if evidence:
            self.emit(step, "check_evidence", started,
                      summary=f"{len(evidence)} gültige Belege aus {total} Zitaten: {verdict}")
            return {"outcome": verdict, "evidence": evidence, "reasoning": prediction.reasoning, "route": "finish"}
        self.emit(step, "check_evidence", started,
                  summary=f"Urteil «{verdict}» verworfen: keines von {total} Zitaten steht im PDF-Text seiner Seite")
        return {"outcome": "nicht_belegt", "evidence": [], "note": DISCARDED_NOTE,
                "reasoning": DISCARDED_REASON.format(verdict=verdict), "route": "finish"}

    def _valid_evidence(self, vote: VoteRef, prediction: PredictionResult) -> List[EvidenceItem]:
        """The quotes found verbatim (after `official_normalize`) in the PDF text of their page, without duplicates."""
        pdf = self.booklet_pdf(vote)
        pages: Dict[int, str] = {}
        seen, items = set(), []
        for source in prediction.evidence_sources:
            page = source.page_number
            if type(page) is not int or page < 1:
                continue
            quote = _PAGE_PREFIX.sub("", source.quote)
            normalized = official_normalize(quote)
            if not normalized or (page, normalized) in seen:
                continue
            if page not in pages:
                text = self.engine.source_page_text(pdf, page)
                pages[page] = official_normalize(text) if text else ""
            if normalized in pages[page]:
                seen.add((page, normalized))
                items.append(EvidenceItem(page=page, quote=quote))
        return items

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
                *, budget: Any = None):
    """The compiled graph. Its input state needs `claim`, `language` and `messages`; see `run_check`.

    `budget` (a `RequestBudget`, optional): if a request was refused during the run, the result is a technical error
    even when the refusal was swallowed on the way (the engine's query translation returns no translation).
    """
    if max_steps < 1:
        raise ValueError(f"max_steps must be at least 1, got {max_steps!r}")
    nodes = _Nodes(tools, decider, engine, tracer, max_steps, budget)
    graph = StateGraph(_State)
    for name in ("decide", "act", "verify", "check_evidence", "finish"):
        graph.add_node(name, nodes.guarded(name, getattr(nodes, name)))
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", _route, {"act": "act", "finish": "finish"})
    graph.add_conditional_edges("act", _route, {"decide": "decide", "verify": "verify", "finish": "finish"})
    graph.add_conditional_edges("verify", _route, {"check_evidence": "check_evidence", "finish": "finish"})
    graph.add_edge("check_evidence", "finish")
    graph.add_edge("finish", END)
    # decide + act for each of at most max_steps + 1 decisions, then verify, check_evidence, finish, plus 2 spare
    return graph.compile().with_config(recursion_limit=2 * (max_steps + 1) + 5)


def _result(state: dict, events: List[TraceEvent]) -> AgentResult:
    common = {"vote": state.get("vote"), "prediction": state.get("prediction"), "events": events,
              "steps": state.get("steps", 0)}
    if state.get("error") is not None or state.get("outcome") in (None, "technischer_fehler"):
        return AgentResult(outcome="technischer_fehler", reasoning=TECHNICAL_REASON,
                           error=state.get("error") or NO_RESULT, **common)
    return AgentResult(outcome=state["outcome"], evidence=list(state.get("evidence", [])),
                       question=state.get("question"), options=list(state.get("options", [])),
                       reasoning=state.get("reasoning", ""), note=state.get("note"), **common)


def run_check(claim: str, *, tools: AgentTools, decider: Decider, engine, tracer: Tracer,
              claim_language: Optional[str] = None, budget: Any = None) -> AgentResult:
    """One check of `claim`. Raises ValueError only for a claim outside 1-1000 characters; every runtime failure is
    an `AgentResult` with `outcome="technischer_fehler"`.

    Without a given vote (`tools.fixed_vote_id`) only claims in `settings.LANGUAGES` are searched; others end as
    `quelle_fehlt` before any LLM call. `claim_language` defaults to `guess_language(claim)`. With `budget` (the
    check's `RequestBudget`) a refused request makes the result a technical error even if it was swallowed.
    """
    if not isinstance(claim, str) or not claim.strip() or len(claim) > settings.MAX_CLAIM_CHARS:
        length = len(claim) if isinstance(claim, str) else type(claim).__name__
        raise ValueError(f"Die Behauptung muss 1 bis {settings.MAX_CLAIM_CHARS} Zeichen lang sein "
                         f"(erhalten: {length}).")
    first_event = len(tracer.events)
    try:
        language = (claim_language or guess_language(claim)).strip().lower()
        if tools.fixed_vote_id is None and language not in settings.LANGUAGES:
            reasoning = LANGUAGE_REASON.format(language=language)
            tracer.emit(step=0, node="finish", tool="quelle_fehlt", args={"sprache": language},
                        summary=f"Ergebnis: quelle_fehlt, Sprache {language} ohne vorgegebene Quelle")
            return AgentResult(outcome="quelle_fehlt", reasoning=reasoning, events=tracer.events[first_event:])
        fixed_vote = tools.check_vote_id(tools.fixed_vote_id) if tools.fixed_vote_id is not None else None
        messages = [{"role": "system", "content": system_prompt(fixed_vote)}, {"role": "user", "content": claim}]
        graph = build_graph(tools, decider, engine, tracer, budget=budget)
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
