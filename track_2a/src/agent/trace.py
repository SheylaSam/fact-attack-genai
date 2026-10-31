"""Run log of the agent: one event per graph node and tool call, in memory and optionally as JSONL lines."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import List, Optional


@dataclass
class TraceEvent:
    step: int  # the decision the event belongs to (0 before the first one)
    node: str  # decide | act | verify | check_evidence | finish
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    summary: str = ""  # short German description of what happened
    duration_ms: float = 0.0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    error: Optional[str] = None


class Tracer:
    """Collects the events of a run. With `jsonl_path` every event is also appended to that file as one JSON line,
    before `emit` returns; a write error propagates (the graph reports it as a technical error)."""

    def __init__(self, jsonl_path: Optional[Path] = None):
        self.jsonl_path = Path(jsonl_path) if jsonl_path is not None else None
        self.events: List[TraceEvent] = []

    def emit(self, **fields) -> TraceEvent:
        event = TraceEvent(**fields)
        self.events.append(event)
        if self.jsonl_path is not None:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self.jsonl_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(asdict(event), ensure_ascii=False, default=str) + "\n")
        return event
