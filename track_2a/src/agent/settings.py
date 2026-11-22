"""Limits and locations of the agent layer."""
import os
from pathlib import Path
from typing import Optional

from src import config

MAX_STEPS = 5  # search steps per check; an invalid step counts too
MAX_RESEARCH_STEPS = 1  # search steps of the Nachsuche after a first "nicht belegt" (graph.py); not part of MAX_STEPS
MAX_CHECKS = 2  # evidence checks per run: the first one and, after "nicht belegt", the one after the Nachsuche
MAX_REQUESTS = 12  # actual HTTP requests per check, retries, translation and cascade included
MAX_PASSAGES = 10  # passages that go into the evidence check
MAX_CLAIM_CHARS = 1000  # the claim is data, never part of the system prompt
LLM_TIMEOUT_S: float = 180.0  # read/write limit of one agent LLM request; connecting is capped at 10 s (decider.py)
LLM_MAX_TOKENS: int = 1024  # output cap of one agent LLM answer (thinking included); a cut-off thinking is no action
LANGUAGES = ("de",)
# The answer to a vote question that none of its options fits (the app's "Keine dieser Vorlagen" button). Never a
# vote id: those are "<source_id>#<k>".
NO_VOTE_ANSWER = "keine"

# "json": the capped smoke of 2026-10-31 (results/genai/agent_tool_smoke.json, 6 requests) found JSON actions valid in 3 of
# 3 situations, while the endpoint rejects native tool calling with HTTP 400 ("auto" tool choice needs the server flags
# --enable-auto-tool-choice and --tool-call-parser). The capped native smoke of 2026-11-04
# (results/genai/agent_native_smoke.json, 4 requests) confirmed it: tool_choice="required" got HTTP 400 in 3 of 3
# ("requires --tool-call-parser to be set"), so its rule keeps json; max_completion_tokens was accepted (HTTP 200).
# "native" stays selectable for an endpoint that supports it.
_TOOL_MODES = ("json", "native")
TOOL_MODE = os.getenv("AGENT_TOOL_MODE", "json")
if TOOL_MODE not in _TOOL_MODES:
    raise ValueError(f"AGENT_TOOL_MODE must be one of {_TOOL_MODES}, got {TOOL_MODE!r}")

# How the search tools run: "lokal" (index and engine in this process) or "mcp" (the own MCP server over stdio). The
# timeouts bound the server start and every single tool call, so a dead or stuck server ends the check as an error.
TOOL_TRANSPORTS = ("lokal", "mcp")
TOOL_TRANSPORT = os.getenv("AGENT_TOOL_TRANSPORT", "lokal")
if TOOL_TRANSPORT not in TOOL_TRANSPORTS:
    raise ValueError(f"AGENT_TOOL_TRANSPORT must be one of {TOOL_TRANSPORTS}, got {TOOL_TRANSPORT!r}")
MCP_START_TIMEOUT_S: float = 120.0  # model and index load in the server process
MCP_CALL_TIMEOUT_S: float = 120.0

INDEX_DIR = Path(os.getenv("AGENT_INDEX_DIR") or config.REPO_ROOT / ".rag-index" / "agent-sources")
TRACE_DIR = Path(os.getenv("AGENT_TRACE_DIR") or config.REPO_ROOT / ".agent-traces")

# Guard thresholds for the vote candidates of a claim (guard.py), calibrated on the dev split of the locked testset
# (scripts/calibrate_guard.py, results/genai/agent_guard_calibration.json; fixed until the configuration freeze of
# stage 5). A candidate is "close" when it trails rank 1 by less than GUARD_SCORE_MARGIN and has at least
# GUARD_MIN_HITS_RATIO times its hits; "keine" below GUARD_NONE_BELOW (None: only without any candidate, not
# calibrated: dev has one case without a vote). Dev result at 0.01 / 0.0: missed_ambiguous 1, false_confidence 0,
# unnecessary_questions 5 (provisional 0.02 / 0.5: 1 / 1 / 6). missed_ambiguous 0 is unreachable on the grid: in
# sdev-02 the second vote is not among the top-5 votes at all (it is assessed mehrdeutig, but with #1 and #3). The
# grid was not extended to force a result. The evidence is thin: the margin step 0.005 -> 0.01 rests on one ambiguous
# case (sdev-01, gap 0.007), and the hits criterion is inactive on dev (0.0 and 0.25 give the same counts; the tie
# rule takes the smaller value), so a 1-hit candidate within the margin counts as close.
GUARD_SCORE_MARGIN: float = 0.01
GUARD_MIN_HITS_RATIO: float = 0.0
GUARD_NONE_BELOW: Optional[float] = None
