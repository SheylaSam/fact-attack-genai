"""Campaign runner of the agent evaluation, the configuration freeze and the gate to the test split.

The logic behind scripts/run_agent_eval.py and scripts/freeze_config.py, without argument parsing.

Campaign (`run_campaign`): one arm over a list of cases, one check per case.
* Total cap: `max_requests` over the whole campaign, resumed parts included. Each check gets its own
  `RequestBudget` of `min(settings.MAX_REQUESTS, what is left)` and its own journal `journals/<id>.requests.jsonl`
  (every request is journaled with fsync before it is sent). A check the campaign cap cut short (its budget refused
  a request and was smaller than the per-check cap) is no result: it and every later case are returned as `NOT_RUN`
  with `CAP_REACHED`, never written to `cases.jsonl` and never silently dropped (`not_run.json`). Its requests count.
* `cases.jsonl`: one record per finished case (the `CaseRun` without the case, plus `id` and `requests`), flushed and
  fsynced after each case. An existing `out_dir` is refused unless `resume`; `resume` skips the cases already in
  `cases.jsonl`, rebuilds their `CaseRun`s and counts the requests spent so far (the larger of the records' sum and
  the journal lines, so a check that crashed before its record counts too) toward the cap.
* After `STOP_AFTER_ERRORS` technical errors in a row the campaign stops and marks the rest `NOT_RUN` with
  `STOPPED_AFTER_ERRORS`: a dead endpoint does not eat the whole cap. A case with an injected fault neither counts
  toward the streak nor resets it (its technical error is expected).
* The runner is generic over the case: it needs `.id` (and reads `.fault` if there is one). The arm builder makes
  the `CaseRun`. Arms are registered by name in `ARMS`.

Arms (`make_arm`), each check with new tools, a new engine, a new budget and a new journal (like
`agent_app.make_agent_session` and `python -m src.agent`):
* B0: the existing check path `engine.verify_claim` on the gold vote's booklet (oracle), vote `None` ("Automatisch").
  A case without a vote ("ohne_abstimmung") and a case with an injected fault get no B0 result: `NOT_SCORED`, no
  request. `scored(runs)` leaves them out before the metrics.
* B1: the agent with only the claim; the scripted user answers a vote question once (`scripted_user.drive`).
* B2: B1 without Nachsuche and without evidence check.
* A case with `fault` gets `faults.faulty_transport` beneath its budget; no other case does. A fault case that sent no
  request is recorded as it came out, with `error = FAULT_NOT_TRIGGERED`.
* `evidence_valid` is computed after the run for `gestuetzt` and `widersprochen` with the graph's own check
  (`graph.valid_evidence`), the same for every arm: True if the graph would keep the verdict (at least one quote
  verbatim on a page of the vote, no cited page without text); such a verdict without evidence is False. None for
  `nicht_belegt` and every non-verdict, in every arm. The outcome never changes.
* `error` is set (short, key and key file redacted) whenever the outcome is `technischer_fehler`.

Freeze: `config_hash` over the git tree SHAs of track_2a/src and track_2a/scripts, the model name, the guard
thresholds, `TOOL_MODE`, the SHA-256 of prompts.py, the testset SHA of the manifest and a snapshot of the non-secret
scalar settings of `src.config` and `src.agent.settings`. `write_freeze` writes the freeze file once, on a clean tree,
after a real dev evaluation with this configuration. `open_test_split` is the only path to the test split. The test
split runs once per arm (one campaign per arm and freeze): a new run is `require_freeze`, `mark_test_run` (a marker per
arm, created exclusively, and the entry `test_runs[arm]` with the out_dir, both fsynced), then
`load_testset("test", allow_test=True)`. After an abort the run of that arm is only resumed, in the same out_dir, never
evaluated anew.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import functools
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx

from src import config
from src.agent import prompts, settings
from src.agent.budget import RequestBudget, install_on_apertus
from src.agent.decider import make_decider
from src.agent.eval_metrics import CaseRun
from src.agent.faults import faulty_transport
from src.agent.graph import LABEL_OUTCOMES, valid_evidence
from src.agent.scripted_user import drive
from src.agent.search_backend import LocalBackend
from src.agent.session import AgentSession, Pending
from src.agent.testset import CASES_PATH, MANIFEST_PATH, load_testset
from src.agent.tools import AgentTools
from src.agent.trace import Tracer
from src.text_utils import guess_language

NOT_RUN = "nicht_gelaufen"  # the case did not run in this campaign (cap or stop); not in cases.jsonl
NOT_SCORED = "nicht_gewertet"  # the arm has no result for this kind of case (B0); in cases.jsonl, left out by `scored`
CAP_REACHED = "not_run_cap_reached"
STOPPED_AFTER_ERRORS = "not_run_after_errors"
FAULT_NOT_TRIGGERED = "fault_not_triggered"
STOP_AFTER_ERRORS = 3
EVIDENCE_OUTCOMES = ("gestuetzt", "widersprochen")  # the verdicts whose evidence is checked
ERROR_CHARS = 200

CASES_NAME, CAMPAIGN_NAME, NOT_RUN_NAME, SUMMARY_NAME = "cases.jsonl", "campaign.json", "not_run.json", "summary.json"
JOURNAL_DIR, TRACE_DIR = "journals", "traces"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")  # a case id is part of file names

PROMPTS_PATH = Path(prompts.__file__)
_TRACK = config.BASE_DIR.relative_to(config.REPO_ROOT).as_posix()  # "track_2a": git paths are relative to the repo
RESULTS_PREFIX = f"{_TRACK}/results/"
FREEZE_SCHEMA = 1
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|ENV_FILE|URL|_PATH$|_DIR$")
_SCALARS = (str, int, float, bool, type(None))

ArmBuilder = Callable[[Any, RequestBudget], CaseRun]


class FreezeError(RuntimeError):
    """The freeze file is missing or does not allow the run; the message is German."""


# --- secrets ----------------------------------------------------------------------------------------------------------

def redact(text: Any) -> str:
    """`text` without the API key and without the path of the key file (both replaced by ***)."""
    text = str(text)
    for secret in (config.LLM_API_KEY, os.environ.get("FACT_ATTACK_ENV_FILE")):
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
            if os.sep in secret:  # the file name alone names the file too
                name = Path(secret).name
                if len(name) >= 4:
                    text = text.replace(name, "***")
    return text


def _short_error(text: Any) -> str:
    text = " ".join(redact(text).split())
    return text if len(text) <= ERROR_CHARS else text[:ERROR_CHARS].rstrip() + "…"


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, data: str) -> None:
    """Replace `path` with `data`, synced to disk before and after the rename."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


# --- arms -------------------------------------------------------------------------------------------------------------

def _default_engine(mock: bool):
    from src.apertus_client import ApertusClient
    from src.inference import ClaimVerificationEngine
    return ClaimVerificationEngine(apertus_client=ApertusClient(mock=mock))


@dataclass
class Wiring:
    """What every check of a campaign shares: the source index (one process per local index), the booklets, whether
    the run is a mock run, where traces go. `engine_factory` (tests) replaces the real engine with a mock or real
    Apertus client; `transport` (tests) replaces the network transport beneath the agent LLM's budget."""
    index: Any
    booklets_dir: Path
    mock: bool
    catalog: Iterable[str] = ()
    trace_dir: Optional[Path] = None
    engine_factory: Optional[Callable[[], Any]] = None
    transport: Optional[Callable[[], httpx.BaseTransport]] = None

    def new_engine(self):
        """A new engine per check: the budget is installed on its client only."""
        return self.engine_factory() if self.engine_factory is not None else _default_engine(self.mock)

    def decider(self, budget: RequestBudget, fault: Optional[str]):
        """Mock: the scripted decider (no request, so no fault can be injected). Otherwise the configured decider;
        with `fault` its transport is the faulty one, beneath the budget."""
        if self.mock:
            return make_decider("scripted", budget)
        inner = self.transport() if self.transport is not None else None
        if fault:
            inner = faulty_transport(inner if inner is not None else httpx.HTTPTransport(), fault)
        return make_decider(settings.TOOL_MODE, budget, transport=inner)

    def write_trace(self, case_id: str, events) -> None:
        if self.trace_dir is None:
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        lines = [redact(json.dumps(asdict(event), ensure_ascii=False, default=str)) for event in events]
        (self.trace_dir / f"{case_id}.trace.jsonl").write_text("".join(line + "\n" for line in lines),
                                                               encoding="utf-8")


def _close(engine) -> None:
    close = getattr(engine, "close", None)
    if close is not None:
        close()


def evidence_validity(outcome: str, vote, quotes: Sequence[Tuple[Any, str]], engine, booklets_dir: Path
                      ) -> Optional[bool]:
    """Post-hoc evidence check of a run, the same for every arm: None unless the outcome is gestuetzt or
    widersprochen (`nicht_belegt` and every non-verdict), False for such a verdict without vote or without quotes,
    else whether the graph's check (`graph.valid_evidence`) would keep the verdict."""
    if outcome not in EVIDENCE_OUTCOMES:
        return None
    if vote is None or not quotes:
        return False
    items, unreadable = valid_evidence(engine, Path(booklets_dir) / f"{vote.source_id}.pdf", vote, quotes)
    return bool(items) and not unreadable


def _technical(outcome: str, error: Optional[str]) -> Optional[str]:
    if outcome != "technischer_fehler":
        return None
    return _short_error(error or "technischer_fehler ohne Meldung")


def _plain_run(case, arm: str, outcome: str, error: Optional[str] = None) -> CaseRun:
    return CaseRun(case=case, arm=arm, outcome=outcome, asked=False, options=(), final_vote_id=None,
                   evidence_valid=None, tokens=0, latency_ms=0.0, tool_calls=0, error=error)


class _AskedRecorder:
    """Stands in for the session in `scripted_user.drive` and keeps every vote question it saw."""

    def __init__(self, session: AgentSession):
        self._session = session
        self.questions: List[Pending] = []

    def _keep(self, turn):
        if isinstance(turn, Pending):
            self.questions.append(turn)
        return turn

    def start(self, claim: str, *args, **kwargs):
        return self._keep(self._session.start(claim, *args, **kwargs))

    def answer(self, vote_id: str):
        return self._keep(self._session.answer(vote_id))


def _agent_case(case, budget: RequestBudget, *, wiring: Wiring, arm: str, nachsuche: bool,
                evidence_check: bool) -> CaseRun:
    """B1/B2: one agent check of `case.claim`, the scripted user answering a vote question once.

    `asked`/`options`: a vote question was asked (answered or not) and what it offered (the first question). A
    question which statement to check (compound claim) is the final result `rueckfrage`, not a vote question."""
    engine = wiring.new_engine()
    try:
        if not wiring.mock:
            install_on_apertus(getattr(engine, "client", None), budget)  # NLI; the LLM is counted by its client
        tools = AgentTools(LocalBackend(wiring.index, engine, wiring.booklets_dir, catalog=wiring.catalog),
                           wiring.booklets_dir)
        session = AgentSession(tools, wiring.decider(budget, getattr(case, "fault", None)), engine, Tracer(),
                               budget=budget, nachsuche=nachsuche, evidence_check=evidence_check)
        user = _AskedRecorder(session)
        started = time.perf_counter()
        turn = drive(user, case)
        latency_ms = (time.perf_counter() - started) * 1000
        events = session.events
        if isinstance(turn, Pending):  # the gold vote was not offered: the question stays unanswered
            outcome, vote, evidence, error = "rueckfrage", None, [], None
        else:
            outcome, vote, evidence, error = turn.outcome, turn.vote, turn.evidence, turn.error
        first = user.questions[0] if user.questions else None
        run = CaseRun(
            case=case, arm=arm, outcome=outcome, asked=first is not None,
            options=tuple(option.vote_id for option in first.options) if first else (),
            final_vote_id=vote.vote_id if vote is not None else None,
            evidence_valid=evidence_validity(outcome, vote, [(item.page, item.quote) for item in evidence], engine,
                                             wiring.booklets_dir),
            tokens=sum(event.tokens_prompt + event.tokens_completion for event in events),
            latency_ms=latency_ms, tool_calls=sum(1 for event in events if event.node in ("act", "verify")),
            error=_technical(outcome, error))
        wiring.write_trace(case.id, events)
        return run
    finally:
        _close(engine)


def _b0_case(case, budget: RequestBudget, *, wiring: Wiring, arm: str = "b0") -> CaseRun:
    """B0: today's check path on the oracle booklet of the gold vote, vote "Automatisch" (None)."""
    if case.vote_id is None or getattr(case, "fault", None):
        return _plain_run(case, arm, NOT_SCORED)  # no booklet to give / no transport to fault: no B0 result
    vote = wiring.index.votes[case.vote_id]
    engine = wiring.new_engine()
    try:
        if not wiring.mock:
            install_on_apertus(getattr(engine, "client", None), budget)
        started = time.perf_counter()
        try:
            booklet = wiring.booklets_dir / f"{vote.source_id}.pdf"
            prediction = engine.verify_claim(claim=case.claim, booklet_pdf=booklet,
                                             claim_language=guess_language(case.claim), vote=None, case_id=case.id)
        except Exception as exc:  # the old path raises on some failures: technical, never a verdict
            return replace(_plain_run(case, arm, "technischer_fehler",
                                      _short_error(f"Ausnahme in der Prüfung: {type(exc).__name__}: {exc}")),
                           final_vote_id=vote.vote_id, latency_ms=(time.perf_counter() - started) * 1000)
        latency_ms = (time.perf_counter() - started) * 1000
        error = prediction.error
        if error is None and getattr(budget, "exceeded", False):
            error = "Anfrage-Budget erschöpft: Mindestens eine Anfrage wurde nicht gesendet."
        if error is None and prediction.label not in LABEL_OUTCOMES:
            error = f"Prüfung lieferte das unbekannte Label {prediction.label!r}."
        outcome = "technischer_fehler" if error is not None else LABEL_OUTCOMES[prediction.label]
        quotes = [(source.page_number, source.quote) for source in prediction.evidence_sources]
        return CaseRun(case=case, arm=arm, outcome=outcome, asked=False, options=(), final_vote_id=vote.vote_id,
                       evidence_valid=evidence_validity(outcome, vote, quotes, engine, wiring.booklets_dir),
                       tokens=prediction.tokens_total, latency_ms=latency_ms, tool_calls=0,
                       error=_technical(outcome, error))
    finally:
        _close(engine)


ARMS: Dict[str, Callable[[Wiring], ArmBuilder]] = {
    "b0": lambda wiring: functools.partial(_b0_case, wiring=wiring, arm="b0"),
    "b1": lambda wiring: functools.partial(_agent_case, wiring=wiring, arm="b1", nachsuche=True, evidence_check=True),
    "b2": lambda wiring: functools.partial(_agent_case, wiring=wiring, arm="b2", nachsuche=False,
                                           evidence_check=False),
}


def _check_arm(arm: str) -> None:
    if arm not in ARMS:
        raise ValueError(f"Unbekannter Arm {arm!r}. Erlaubt: {', '.join(ARMS)}.")


def make_arm(arm: str, wiring: Wiring) -> ArmBuilder:
    """The per-case builder of `arm` over `wiring`."""
    _check_arm(arm)
    return ARMS[arm](wiring)


def scored(runs: Sequence[CaseRun]) -> List[CaseRun]:
    """The runs the metrics take: without `NOT_SCORED` (B0 has no result for a case without a vote or with a fault).
    `NOT_RUN` stays in: a case the campaign did not run is an abstention, never a silently smaller test set."""
    return [run for run in runs if run.outcome != NOT_SCORED]


# --- campaign ---------------------------------------------------------------------------------------------------------

def _record(run: CaseRun, requests: int) -> dict:
    return {"id": run.case.id, "arm": run.arm, "outcome": run.outcome, "asked": run.asked,
            "options": list(run.options), "final_vote_id": run.final_vote_id, "evidence_valid": run.evidence_valid,
            "tokens": run.tokens, "latency_ms": run.latency_ms, "tool_calls": run.tool_calls, "error": run.error,
            "requests": requests}


def _from_record(case, record: dict) -> CaseRun:
    return CaseRun(case=case, arm=record["arm"], outcome=record["outcome"], asked=record["asked"],
                   options=tuple(record["options"]), final_vote_id=record["final_vote_id"],
                   evidence_valid=record["evidence_valid"], tokens=record["tokens"], latency_ms=record["latency_ms"],
                   tool_calls=record["tool_calls"], error=record["error"])


def read_records(out_dir: Path) -> List[dict]:
    """The records of `cases.jsonl` in `out_dir` (empty if there is none). ValueError names a broken line."""
    path = Path(out_dir) / CASES_NAME
    if not path.exists():
        return []
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"{path}, Zeile {number}: kein gültiges JSON (abgebrochener Lauf?). Bitte prüfen; "
                             "die Anfragen stehen im Journal.") from None
        found.append(record)
    return found


def requests_spent(out_dir: Path) -> int:
    """Requests spent in `out_dir` so far: the larger of the records' sum and the journal lines (a check that crashed
    before its record was written has journal lines only)."""
    out_dir = Path(out_dir)
    recorded = sum(int(record.get("requests", 0)) for record in read_records(out_dir))
    journaled = sum(len([line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()])
                    for path in (out_dir / JOURNAL_DIR).glob("*.jsonl")) if (out_dir / JOURNAL_DIR).is_dir() else 0
    return max(recorded, journaled)


def _journal_path(journal_dir: Path, case_id: str) -> Path:
    """A new journal per check; a check repeated after a crash gets the next free name, the old one stays."""
    path = journal_dir / f"{case_id}.requests.jsonl"
    attempt = 1
    while path.exists():
        attempt += 1
        path = journal_dir / f"{case_id}.attempt{attempt}.requests.jsonl"
    return path


def _case_ids(cases: Sequence[Any]) -> List[str]:
    ids = [str(case.id) for case in cases]
    for case_id in ids:
        if not _SAFE_ID.fullmatch(case_id):
            raise ValueError(f"Fall-ID {case_id!r} taugt nicht als Dateiname")
    if len(set(ids)) != len(ids):
        raise ValueError("Doppelte Fall-IDs in der Kampagne")
    return ids


def _ids_sha(ids: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def _start(arm: str, ids: List[str], max_requests: int, out_dir: Path, resume: bool) -> Dict[str, dict]:
    """Creates `out_dir` (refusing an existing one without `resume`) or checks that it is this campaign's; returns
    the finished records by id."""
    campaign_path = out_dir / CAMPAIGN_NAME
    if out_dir.exists() and not resume:
        raise FileExistsError(f"{out_dir} existiert schon. Ein bestehender Lauf wird nie überschrieben "
                              "(fortsetzen: --resume).")
    if resume and campaign_path.exists():
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        if campaign.get("arm") != arm or campaign.get("case_ids_sha256") != _ids_sha(ids):
            raise ValueError(f"{out_dir} gehört zu einem anderen Lauf (Arm {campaign.get('arm')!r} oder andere "
                             "Fälle)")
        finished = {record["id"]: record for record in read_records(out_dir)}
        if unknown := sorted(set(finished) - set(ids)):
            raise ValueError(f"{out_dir}: Ergebnisse zu unbekannten Fällen: {', '.join(unknown[:5])}")
        return finished
    if resume and out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"{out_dir} ist kein Lauf dieses Runners ({CAMPAIGN_NAME} fehlt)")
    out_dir.mkdir(parents=True, exist_ok=resume)
    with campaign_path.open("x", encoding="utf-8") as stream:
        json.dump({"arm": arm, "case_ids_sha256": _ids_sha(ids), "cases": len(ids), "max_requests": max_requests,
                   "created_at": datetime.now(timezone.utc).isoformat()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return {}


def run_campaign(arm: str, cases: Sequence[Any], *, build_arm: ArmBuilder, max_requests: int, out_dir: Path,
                 resume: bool = False) -> List[CaseRun]:
    """Runs `build_arm(case, budget)` for every case under a total cap of `max_requests` HTTP requests; returns one
    `CaseRun` per case, in the order of `cases` (see the module docstring for cap, journal, resume and not-run).
    Raises FileExistsError for an existing `out_dir` without `resume`, ValueError for an unknown arm, a bad or
    repeated case id or an `out_dir` of another campaign. An exception of `build_arm` ends the campaign: the cases
    finished so far are in `cases.jsonl`, the requests in the journals, and `resume` continues."""
    _check_arm(arm)
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError(f"max_requests muss eine ganze Zahl ab 0 sein, nicht {max_requests!r}")
    ids = _case_ids(cases)
    out_dir = Path(out_dir)
    finished = _start(arm, ids, max_requests, out_dir, resume)
    spent = requests_spent(out_dir)
    journal_dir = out_dir / JOURNAL_DIR
    runs: List[CaseRun] = []
    not_run: List[dict] = []
    stop_reason: Optional[str] = None
    errors_in_row = 0
    with (out_dir / CASES_NAME).open("a", encoding="utf-8") as results:
        for case_id, case in zip(ids, cases):
            if case_id in finished:
                runs.append(_from_record(case, finished[case_id]))
                continue
            left = max_requests - spent
            if stop_reason is None and left <= 0:
                stop_reason = CAP_REACHED
            if stop_reason is not None:
                runs.append(_plain_run(case, arm, NOT_RUN, stop_reason))
                not_run.append({"id": case_id, "reason": stop_reason})
                continue
            limit = min(settings.MAX_REQUESTS, left)
            budget = RequestBudget(limit=limit, journal_path=_journal_path(journal_dir, case_id))
            run = build_arm(case, budget)
            spent += budget.used
            if budget.exceeded and limit < settings.MAX_REQUESTS:  # cut short by the campaign cap: no result
                stop_reason = CAP_REACHED
                runs.append(_plain_run(case, arm, NOT_RUN, stop_reason))
                not_run.append({"id": case_id, "reason": stop_reason, "requests": budget.used})
                continue
            if getattr(case, "fault", None) and budget.used == 0 and run.outcome != NOT_SCORED:
                # the injected fault never hit a request (e.g. an early result): shown, not disguised
                run = replace(run, error=FAULT_NOT_TRIGGERED + (f": {run.error}" if run.error else ""))
            if run.error is not None:
                run = replace(run, error=_short_error(run.error))
            if run.outcome == "technischer_fehler" and run.error is None:
                run = replace(run, error="technischer_fehler ohne Meldung")
            results.write(json.dumps(_record(run, budget.used), ensure_ascii=False) + "\n")
            results.flush()
            os.fsync(results.fileno())
            runs.append(run)
            if getattr(case, "fault", None):  # its technical error is expected: neither counts nor resets
                continue
            if run.outcome == "technischer_fehler":
                errors_in_row += 1
                if errors_in_row >= STOP_AFTER_ERRORS:
                    stop_reason = STOPPED_AFTER_ERRORS
            else:
                errors_in_row = 0
    _write_atomic(out_dir / NOT_RUN_NAME, json.dumps(not_run, ensure_ascii=False, indent=2) + "\n")
    return runs


# --- freeze -----------------------------------------------------------------------------------------------------------

def _git(*args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(config.REPO_ROOT), *args], check=True, capture_output=True,
                                text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise FreezeError(f"git {' '.join(args)} ist fehlgeschlagen: {detail.strip()}") from None
    return result.stdout


def _changed_paths(status: str) -> List[str]:
    """The paths in `git status --porcelain -z` output (a rename or copy lists both)."""
    paths: List[str] = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            paths.append(next(entries, ""))
    return [path for path in paths if path]


def tree_changes() -> List[str]:
    """Changed or untracked paths outside track_2a/results/ (empty: the tree is clean in the sense of the freeze)."""
    return [path for path in _changed_paths(_git("status", "--porcelain", "-z", "--untracked-files=all"))
            if not path.startswith(RESULTS_PREFIX)]


def _settings_snapshot() -> Dict[str, Any]:
    """The non-secret scalar settings of `src.config` and `src.agent.settings` (environment overrides included);
    names with KEY, TOKEN, SECRET, PASSW, ENV_FILE, URL, paths and directories are left out, and so is any value that
    contains the API key or the key file's path."""
    secrets = [value for value in (config.LLM_API_KEY, os.environ.get("FACT_ATTACK_ENV_FILE")) if value]
    snapshot = {}
    for module in (config, settings):
        for name, value in sorted(vars(module).items()):
            if not name.isupper() or name.startswith("_") or _SECRET_NAME.search(name):
                continue
            if not (isinstance(value, _SCALARS) or (isinstance(value, (list, tuple))
                                                    and all(isinstance(item, _SCALARS) for item in value))):
                continue
            if any(secret in json.dumps(value, default=str) for secret in secrets):  # never hash a secret value
                continue
            snapshot[f"{module.__name__}.{name}"] = list(value) if isinstance(value, tuple) else value
    return snapshot


def _manifest() -> dict:
    try:
        manifest = json.loads(Path(MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FreezeError(f"Manifest {MANIFEST_PATH} fehlt oder ist nicht lesbar: das Testset ist nicht gesperrt") \
            from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases_sha256"), str):
        raise FreezeError(f"Manifest {MANIFEST_PATH} ohne cases_sha256")
    return manifest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def config_components() -> Dict[str, Any]:
    """What the configuration hash covers (no secret: the snapshot enters as a hash)."""
    return {
        "src_tree": _git("rev-parse", f"HEAD:{_TRACK}/src").strip(),
        "scripts_tree": _git("rev-parse", f"HEAD:{_TRACK}/scripts").strip(),
        "model": config.LLM_NAME,
        "guard": [settings.GUARD_SCORE_MARGIN, settings.GUARD_MIN_HITS_RATIO, settings.GUARD_NONE_BELOW],
        "tool_mode": settings.TOOL_MODE,
        "prompts_sha256": _sha256(Path(PROMPTS_PATH).read_bytes()),
        "testset_sha256": _manifest()["cases_sha256"],
        "settings_sha256": _sha256(json.dumps(_settings_snapshot(), sort_keys=True, default=str).encode("utf-8")),
    }


def _components_hash(components: Dict[str, Any]) -> str:
    return _sha256(json.dumps(components, sort_keys=True).encode("utf-8"))


def config_hash() -> str:
    """SHA-256 over `config_components()`. The commit SHA is not part of it: committing results changes nothing."""
    return _components_hash(config_components())


def _marker(path: Path, arm: str) -> Path:
    return path.with_name(f"{path.name}.test_run.{arm}")


def _markers(path: Path) -> List[Path]:
    return sorted(path.parent.glob(glob.escape(path.name) + ".test_run.*"))


def _out_key(out_dir: Path) -> str:
    return str(Path(out_dir).resolve())


def _read_freeze(path: Path) -> dict:
    path = Path(path)
    try:
        freeze = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FreezeError(f"Die Freeze-Datei {path} fehlt (scripts/freeze_config.py).") from None
    except (OSError, ValueError):
        raise FreezeError(f"Die Freeze-Datei {path} ist nicht lesbar.") from None
    if not isinstance(freeze, dict) or not isinstance(freeze.get("config_hash"), str):
        raise FreezeError(f"Die Freeze-Datei {path} hat keinen config_hash.")
    if not isinstance(freeze.get("test_runs", {}), dict):
        raise FreezeError(f"Die Freeze-Datei {path} hat ein unlesbares test_runs.")
    return freeze


def _test_run(path: Path, freeze: dict, arm: str) -> Optional[dict]:
    """The recorded test run of `arm`, or None. The marker file (written first, exclusively) and the entry in the
    freeze file are both records; if both exist they must name the same out_dir."""
    entry = freeze.get("test_runs", {}).get(arm)
    marker = _marker(path, arm)
    marked = None
    if marker.exists():
        try:
            marked = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise FreezeError(f"Der Marker {marker.name} ist nicht lesbar; bitte prüfen.") from None
    if entry is not None and marked is not None and entry.get("out_dir") != marked.get("out_dir"):
        raise FreezeError(f"Freeze-Eintrag und Marker des Arms {arm} nennen verschiedene Ordner; bitte prüfen.")
    return entry if entry is not None else marked


def _require_clean_tree() -> None:
    if changes := tree_changes():
        raise FreezeError(f"Der Arbeitsbaum ist nicht sauber ({len(changes)} Änderungen ausserhalb von "
                          f"{RESULTS_PREFIX}, z. B. {changes[0]}). Erst committen.")


def _check_dev_run(dev_run: Path, current: str) -> dict:
    path = Path(dev_run) / SUMMARY_NAME
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FreezeError(f"Keine Dev-Evaluation in {dev_run} ({SUMMARY_NAME} fehlt oder ist nicht lesbar).") \
            from None
    problems = []
    if summary.get("split") != "dev":
        problems.append(f"Split ist {summary.get('split')!r}, nicht 'dev'")
    if summary.get("mock") is not False:
        problems.append("der Lauf war ein Mock-Lauf")
    if summary.get("tree_clean") is not True:
        problems.append("der Lauf lief auf einem unsauberen Arbeitsbaum")
    if summary.get("config_hash") != current:
        problems.append("der Lauf lief mit einer anderen Konfiguration (config_hash weicht ab)")
    if problems:
        raise FreezeError(f"Die Dev-Evaluation {dev_run} taugt nicht für den Freeze: {'; '.join(problems)}.")
    return summary


def write_freeze(path: Path, dev_run: Path) -> dict:
    """Writes the freeze file once (never overwritten): clean tree, and a real dev evaluation in `dev_run` that ran
    with this configuration on a clean tree. Returns what was written."""
    path = Path(path)
    if path.exists() or _markers(path):
        raise FreezeError(f"{path} existiert schon; ein Freeze wird nie überschrieben.")
    _require_clean_tree()
    components = config_components()
    current = _components_hash(components)
    summary = _check_dev_run(dev_run, current)
    freeze = {"schema": FREEZE_SCHEMA, "config_hash": current, "components": components,
              "git_commit": _git("rev-parse", "HEAD").strip(),  # for information only
              "frozen_at": datetime.now(timezone.utc).isoformat(), "dev_run": str(dev_run),
              "dev_run_arm": summary.get("arm"),
              "dev_run_summary_sha256": _sha256((Path(dev_run) / SUMMARY_NAME).read_bytes()), "test_runs": {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(freeze, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise FreezeError(f"{path} existiert schon; ein Freeze wird nie überschrieben.") from None
    _fsync_dir(path.parent)
    return freeze


def require_freeze(path: Path, arm: Optional[str] = None, *, resume_out: Optional[Path] = None) -> dict:
    """Raises FreezeError unless the freeze allows the run; returns the freeze.

    Always: the freeze file exists, the tree is clean and the configuration hash matches. Then, per arm (one test
    campaign per arm and freeze):
    * a new run of `arm` (`resume_out` None): `arm` has no recorded test run (entry or marker);
    * a resumed run (`resume_out`): `arm` has a recorded test run and it was started in `resume_out`.
    Without `arm`: no arm may have a recorded test run."""
    path = Path(path)
    freeze = _read_freeze(path)
    if arm is None:
        if resume_out is not None:
            raise ValueError("resume_out braucht einen Arm")
        if freeze.get("test_runs") or _markers(path):
            raise FreezeError("Mit diesem Freeze wurde der Testteil bereits ausgewertet.")
    else:
        _check_arm(arm)
        recorded = _test_run(path, freeze, arm)
        if resume_out is None and recorded is not None:
            raise FreezeError(f"Der Testteil wurde mit Arm {arm} bereits begonnen (Ordner {recorded.get('out_dir')}); "
                              "ein zweiter Lauf ist nicht erlaubt, nur die Wiederaufnahme dort (--resume).")
        if resume_out is not None and recorded is None:
            raise FreezeError(f"Für Arm {arm} gibt es keinen begonnenen Testlauf; --resume ist nicht möglich.")
        if resume_out is not None and recorded.get("out_dir") != _out_key(resume_out):
            raise FreezeError(f"Der Testlauf von Arm {arm} gehört zum Ordner {recorded.get('out_dir')}, nicht zu "
                              f"{resume_out}; fortgesetzt wird nur dort.")
    _require_clean_tree()
    components = config_components()
    if _components_hash(components) != freeze["config_hash"]:
        changed = sorted(name for name, value in components.items()
                         if (freeze.get("components") or {}).get(name) != value)
        raise FreezeError("Die Konfiguration weicht vom Freeze ab"
                          + (f" ({', '.join(changed)})" if changed else "") + ".")
    return freeze


def mark_test_run(path: Path, arm: str, *, out_dir: Path) -> None:
    """Records the test run of `arm` before its first case: a marker file per arm, created exclusively (two runs of
    one arm cannot both get it), and the entry `test_runs[arm]` in the freeze file, both synced to disk. The entry
    names `out_dir`, the only place the run may be resumed."""
    path = Path(path)
    _check_arm(arm)
    if _test_run(path, _read_freeze(path), arm) is not None:
        raise FreezeError(f"Der Testteil wurde mit Arm {arm} bereits begonnen.")
    entry = {"arm": arm, "started_at": datetime.now(timezone.utc).isoformat(), "out_dir": _out_key(out_dir)}
    try:
        with _marker(path, arm).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise FreezeError(f"Der Testteil wurde mit Arm {arm} bereits begonnen (Marker vorhanden).") from None
    _fsync_dir(path.parent)
    freeze = _read_freeze(path)  # read again right before writing: another arm may have added its entry
    runs = {**freeze.get("test_runs", {}), arm: entry}
    _write_atomic(path, json.dumps({**freeze, "test_runs": runs}, indent=2, ensure_ascii=False) + "\n")


def open_test_split(freeze_path: Path, arm: str, *, out_dir: Path, resume: bool = False) -> list:
    """The only way to the test split. A new run: `require_freeze`, `mark_test_run`, then the cases. A resumed run
    (after an abort; never a second evaluation): `require_freeze` with this arm's recorded run in `out_dir`, then the
    cases."""
    if resume:
        require_freeze(freeze_path, arm, resume_out=out_dir)
    else:
        require_freeze(freeze_path, arm)
        mark_test_run(freeze_path, arm, out_dir=out_dir)
    return load_testset("test", cases_path=CASES_PATH, manifest_path=MANIFEST_PATH, allow_test=True)


def load_dev() -> list:
    return load_testset("dev", cases_path=CASES_PATH, manifest_path=MANIFEST_PATH)


def test_case_count() -> int:
    """The number of test cases from the manifest, without reading the cases."""
    counts = (_manifest().get("counts") or {}).get("split_group") or {}
    return sum(int(count) for key, count in counts.items() if str(key).startswith("test/"))



def write_summary(out_dir: Path, *, arm: str, split: str, mock: bool, runs: Sequence[CaseRun], max_requests: int,
                  freeze: Optional[Path] = None) -> dict:
    """`summary.json` of a campaign: what ran with which configuration (freeze_config.py reads it)."""
    out_dir = Path(out_dir)
    try:
        current: Optional[str] = config_hash()
        clean: Optional[bool] = not tree_changes()
    except FreezeError:
        current, clean = None, None
    outcomes: Dict[str, int] = {}
    for run in runs:
        outcomes[run.outcome] = outcomes.get(run.outcome, 0) + 1
    summary = {"arm": arm, "split": split, "mock": mock, "config_hash": current, "tree_clean": clean,
               "max_requests": max_requests, "requests_spent": requests_spent(out_dir), "cases": len(runs),
               "finished": sum(1 for run in runs if run.outcome != NOT_RUN),
               "not_run": [run.case.id for run in runs if run.outcome == NOT_RUN], "outcomes": outcomes,
               "freeze": str(freeze) if freeze else None, "written_at": datetime.now(timezone.utc).isoformat()}
    _write_atomic(out_dir / SUMMARY_NAME, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary
