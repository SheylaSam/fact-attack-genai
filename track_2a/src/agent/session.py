"""One check across a question and its answer: the interactive mode of the agent graph.

    session = AgentSession(tools, decider, engine, tracer, budget=budget)
    turn = session.start(claim)        # a `Pending` question, or the finished `AgentResult`
    result = session.answer(vote_id)   # only after a `Pending`; continues the same check
    turn = session.start(claim, last_vote=vote)  # session memory: a hint and one more answer, never the choice

`start` runs the check up to the first question (the guard's or the model's). The graph pauses there with
`interrupt`, nothing is checked yet, and the question comes back as `Pending`. `answer` resumes the paused graph with
the chosen vote: the vote is fixed in `tools` and the check goes on from the state the pause saved. Every turn that
does not end in a question is an `AgentResult`; its `events` hold the whole check, from before and after the answer.

One session is one check at a time. `tools`, `decider`, `engine` and `tracer` must stay the same instances from
`start` to `answer`: the vote pool, the guard's cache, the fixed vote and the template list live in `tools`, not in the
checkpoint, and a new tracer would split the events. `tools` carries the fixed vote of an answered check, so a new
check after an answer needs new `tools`; `start` while a question is open drops that question (its thread is not
resumed again) and starts a new check with the same `tools`. Sync only, like the graph.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
import uuid

from langgraph.types import Command

from src.agent import settings
from src.agent.decider import Decider
from src.agent.graph import (AgentResult, _result, _validate_claim, build_graph, crash_result, make_checkpointer,
                             prepare, usable_last_vote)
from src.agent.source_index import VoteRef
from src.agent.tools import AgentTools
from src.agent.trace import TraceEvent, Tracer

NO_QUESTION = "Es gibt keine offene Rückfrage."
NOT_AN_OPTION = "Die Antwort {answer} gehört nicht zu den angebotenen Vorlagen ({options})."


@dataclass(frozen=True)
class Pending:
    """The open question of a paused check: what to ask and which votes may answer: the options and, if the check
    was started with a usable `last_vote` (the session memory), that vote too, even when it is no option."""
    question: str
    options: Tuple[VoteRef, ...]
    asked_by: str  # "guard" or "model"
    last_vote: Optional[VoteRef] = None


Turn = Union[Pending, AgentResult]


class AgentSession:
    def __init__(self, tools: AgentTools, decider: Decider, engine, tracer: Tracer, *, budget=None,
                 nachsuche: bool = True, max_steps: int = settings.MAX_STEPS):
        self._tools = tools
        self._tracer = tracer
        self._graph = build_graph(tools, decider, engine, tracer, max_steps, budget=budget, nachsuche=nachsuche,
                                  checkpointer=make_checkpointer())
        self._config: Optional[dict] = None  # the thread of the current check
        self._first_event = len(tracer.events)
        self._pending: Optional[Pending] = None
        self._last_vote: Optional[VoteRef] = None  # the memory of the current check (see `start`)

    @property
    def pending(self) -> Optional[Pending]:
        """The open question, or None."""
        return self._pending

    @property
    def events(self) -> List[TraceEvent]:
        """The events of the current check so far (the tracer's events from its `start` on)."""
        return self._tracer.events[self._first_event:]

    def start(self, claim: str, claim_language: Optional[str] = None, *, last_vote: Optional[VoteRef] = None) -> Turn:
        """Starts a check. Raises ValueError for a claim outside 1-`settings.MAX_CLAIM_CHARS` characters; every
        runtime failure is an `AgentResult` with `technischer_fehler`. An open question is dropped.

        `last_vote` is the session memory, the vote the person chose last. It is a hint for the model (one line after
        the claim) and, if the check asks, the one answer outside the options that `answer` accepts: the app offers it
        as an extra button. It never fixes the vote and the guard still asks. A vote these tools do not know and a
        check whose vote is given leave it out (`graph.usable_last_vote`)."""
        _validate_claim(claim)  # the caller's error: raised, unlike the failures inside the run
        self._pending = None
        self._last_vote = usable_last_vote(self._tools, last_vote)
        self._first_event = len(self._tracer.events)
        self._config = {"configurable": {"thread_id": uuid.uuid4().hex}}
        try:
            start = prepare(claim, self._tools, self._tracer, claim_language, self._last_vote)
            if isinstance(start, AgentResult):  # an early result: no graph run
                return start
            return self._turn(self._graph.invoke(start, self._config))
        except Exception as exc:
            return crash_result(exc, self._tracer, self._first_event)

    def answer(self, vote_id: str) -> Turn:
        """Answers the open question with one of its options, or with its `last_vote`, and continues the check.
        Raises ValueError, and keeps the question open, if there is no question or `vote_id` is none of those. The
        answer is used up either way: a second `answer` raises ValueError."""
        pending = self._pending
        if pending is None:
            raise ValueError(NO_QUESTION)
        ids = [option.vote_id for option in pending.options]
        if pending.last_vote is not None:
            ids.append(pending.last_vote.vote_id)
        if vote_id not in ids:
            raise ValueError(NOT_AN_OPTION.format(answer=repr(vote_id)[:80], options=", ".join(ids)))
        self._pending = None  # before the run: whatever happens, this question is answered
        try:
            return self._turn(self._graph.invoke(Command(resume=vote_id), self._config))
        except Exception as exc:
            return crash_result(exc, self._tracer, self._first_event)

    def _turn(self, state: dict) -> Turn:
        """A question if the graph paused, else the result of the whole check."""
        interrupts = state.get("__interrupt__")
        if not interrupts:
            return _result(state, self.events)
        payload = interrupts[0].value
        options = tuple(self._tools.check_vote_id(vote_id) for vote_id in payload["options"])
        self._pending = Pending(payload["question"], options, payload["asked_by"], self._last_vote)
        return self._pending
