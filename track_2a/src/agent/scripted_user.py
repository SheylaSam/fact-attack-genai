"""The scripted user of the evaluation: answers a vote question with the gold vote, once, and never anything else.

`EvalCase.vote_id` is the gold vote (the vote the evidence is from). `EvalCase.after_answer` is NOT a vote: it is the
verdict the case expects once the question is answered with `vote_id`.
"""
from typing import Optional

from src.agent.session import AgentSession, Pending, Turn
from src.agent.testset import EvalCase


def answer_for(case: EvalCase, pending: Pending) -> Optional[str]:
    """The gold vote if it is one of the question's options, else None (the user cannot give the right answer).
    The session memory's `last_vote` is no option here: the scripted user only picks from what is offered."""
    offered = {option.vote_id for option in pending.options}
    return case.vote_id if case.vote_id in offered else None


def drive(session: AgentSession, case: EvalCase) -> Turn:
    """Starts the check of `case.claim`, answers a vote question at most once and returns the final turn.

    The gold vote is not offered: the question stays unanswered and is returned as it is (a wrong question, no second
    attempt). A question after the answer is returned unanswered too. A question for the statement of a compound claim
    (an `AgentResult` with `statements`) is the final turn: the person would choose a statement, the scripted user
    does not."""
    turn = session.start(case.claim)
    if not isinstance(turn, Pending):
        return turn
    vote_id = answer_for(case, turn)
    if vote_id is None:
        return turn
    return session.answer(vote_id)
