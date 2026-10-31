"""Limits and locations of the agent layer."""
import os
from pathlib import Path

from src import config

MAX_STEPS = 5  # search steps per check; an invalid step counts too
MAX_REQUESTS = 12  # actual HTTP requests per check, retries, translation and cascade included
MAX_PASSAGES = 10  # passages that go into the evidence check
MAX_CLAIM_CHARS = 1000  # the claim is data, never part of the system prompt
LANGUAGES = ("de",)

_TOOL_MODES = ("json", "native")
TOOL_MODE = os.getenv("AGENT_TOOL_MODE", "json")
if TOOL_MODE not in _TOOL_MODES:
    raise ValueError(f"AGENT_TOOL_MODE must be one of {_TOOL_MODES}, got {TOOL_MODE!r}")

INDEX_DIR = Path(os.getenv("AGENT_INDEX_DIR") or config.REPO_ROOT / ".rag-index" / "agent-sources")
TRACE_DIR = Path(os.getenv("AGENT_TRACE_DIR") or config.REPO_ROOT / ".agent-traces")
