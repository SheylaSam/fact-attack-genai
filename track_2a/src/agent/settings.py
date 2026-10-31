"""Limits and locations of the agent layer."""
import os
from pathlib import Path

from src import config

MAX_STEPS = 5  # search steps per check; an invalid step counts too
MAX_REQUESTS = 12  # actual HTTP requests per check, retries, translation and cascade included
MAX_PASSAGES = 10  # passages that go into the evidence check
MAX_CLAIM_CHARS = 1000  # the claim is data, never part of the system prompt
LLM_TIMEOUT_S: float = 180.0  # read/write limit of one agent LLM request; connecting is capped at 10 s (decider.py)
LANGUAGES = ("de",)

# "json": the capped smoke of 2026-10-31 (results/genai/agent_tool_smoke.json, 6 requests) found JSON actions valid in 3 of
# 3 situations, while the endpoint rejects native tool calling with HTTP 400 ("auto" tool choice needs the server flags
# --enable-auto-tool-choice and --tool-call-parser). "native" stays selectable for an endpoint that supports it.
_TOOL_MODES = ("json", "native")
TOOL_MODE = os.getenv("AGENT_TOOL_MODE", "json")
if TOOL_MODE not in _TOOL_MODES:
    raise ValueError(f"AGENT_TOOL_MODE must be one of {_TOOL_MODES}, got {TOOL_MODE!r}")

INDEX_DIR = Path(os.getenv("AGENT_INDEX_DIR") or config.REPO_ROOT / ".rag-index" / "agent-sources")
TRACE_DIR = Path(os.getenv("AGENT_TRACE_DIR") or config.REPO_ROOT / ".agent-traces")
