"""Capped real smoke of native tool calling with `tool_choice="required"` and of the completion cap.

Two questions, four HTTP requests, counted on ONE `RequestBudget(limit=4)` with a journal (fsync before every send, SDK
retries off). The prompt is the graph's real one (`prompts.system_prompt`), the tools are the real `TOOL_SPECS`
(seven since stage 5), offered in every situation.

| No. | Mode            | Situation | Purpose                                          |
|-----|-----------------|-----------|--------------------------------------------------|
| 1   | JSON            | a         | first real request with `max_completion_tokens`  |
| 2-4 | native required | a, b, c   | tool calling                                     |

* (a) a new claim, no history            -> `suche_vorlagen` expected
* (b) two equally good Mietrecht votes   -> `rueckfrage` expected
* (c) passages found for the right vote  -> `pruefen` expected

The situations are those of stage 1 with the observations of stage 3: `suche_passagen` has no `anfrage` and searches
with the claim itself. The observation texts are hard-coded (see the comment at the constants), so the smoke needs
neither index nor model.

A recording transport keeps per request only: the sorted top-level keys of the JSON body, the value of `tool_choice`,
the value of `max_completion_tokens` and the HTTP status. Never the body text, the messages or any header.

If the endpoint rejects request 1 with HTTP 400, the run stops: the remaining rows are `skipped`.

The run is paid and starts only with `--confirm-paid` and `FACT_ATTACK_ENV_FILE` (the .env with the API key, read by
`src.config`; its content never goes into the report). The result file and the journal are never overwritten: a second
paid run needs a decision, not a repeated command.

Usage (from track_2a/):
  FACT_ATTACK_ENV_FILE=<path to .env> ../.venv312/bin/python scripts/agent_native_smoke.py --confirm-paid
Exit code: 0 all four requests were made, 1 refused (nothing sent) or failed, 2 stopped early (key or endpoint
rejected, request 1 refused with HTTP 400).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from agent_tool_smoke import Situation, _scrub, run_one  # stage 1 stays unchanged
from src import config
from src.agent import settings
from src.agent.budget import RequestBudget
from src.agent.decider import JsonActionDecider, NativeToolDecider, make_chat_model
from src.agent.tools import Observation

MAX_REQUESTS = 4
EXIT_DONE, EXIT_REFUSED, EXIT_STOPPED = 0, 1, 2
DEFAULT_OUTPUT = ROOT / "results" / "genai" / "agent_native_smoke.json"
RULE = ("native wird Standard nur, wenn alle 3 nativen Anfragen mit tool_choice=required angenommen werden, 3 von 3 "
        "ein angebotenes Werkzeug mit gültigen Argumenten liefern und 3 von 3 das erwartete Werkzeug wählen; sonst "
        "bleibt json.")

CLAIM_AMBIGUOUS = "Die Mietrechtsvorlage stärkt die Vermieter."
CLAIM_SUBLET = ("Bei der Untermiete müssen Mieterinnen und Mieter künftig die schriftliche Zustimmung der "
                "Vermieterin oder des Vermieters einholen.")
SUBLET_VOTE = "2024-11-24_de#2"

# The observation texts below are the real outputs of the stage-3 `AgentTools` (`LocalBackend`) over the real source
# index (.rag-index/agent-sources), with the mock engine and no LLM call, generated on 2026-11-04 by running, for each
# claim, `tools.run("suche_vorlagen", {"anfrage": claim}, claim=claim)` and for the sublet claim then
# `tools.run("suche_passagen", {"vorlage_id": SUBLET_VOTE}, claim=claim)` (the proven search takes no `anfrage`). The
# votes observations end with the guard's assessment, as the real tool writes it.
VOTES_AMBIGUOUS = """Gefundene Vorlagen, beste zuerst. Score = ähnlichste Textstelle der Vorlage, Treffer = Textstellen der Vorlage unter den 50 ähnlichsten.
1. 2024-11-24_de#3 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) | Score 0.867 | Treffer 15 | Seite 40 | «40 Dritte Vorlage: Kündigung wegen Eigenbedarfs Argumente Bundesrat und Parlament Eigentümerinnen und Eigentümer sollen…»
2. 2020-02-09_de#1 | 09.02.2020 | Volksinitiativ e «Mehr bezahlbare Wohnungen» | Score 0.860 | Treffer 13 | Seite 13 | «13 Die Initiative verlangt, dass mehr Areale für gemein nützige Wohnungen zur Verfügung stehen. Dafür braucht es vor all…»
3. 2024-11-24_de#2 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Untermiete) | Score 0.860 | Treffer 14 | Seite 29 | «29 Der Text auf dieser Doppelseite stammt vom Referendumskomitee. Es ist für den Inhalt und die Wortwahl verantwortlich.…»
4. 2021-11-28_de#3 | 28.11.2021 | Änderung vom 19. März 2021 des Covid-19-Gesetzes | Score 0.848 | Treffer 1 | Seite 36 | «beschlossene Gesetz setzt dem Bundesrat klare Leitplanken, ermöglicht die ge meinsame Krisenbewältigung und sichert die…»
5. 2022-05-15_de#3 | 15.05.2022 | Übernahme der EU-Verordnung über die Europäische Grenz- und Küstenwache (Weiterentwicklung des Schengen-Besitzstands) | Score 0.845 | Treffer 3 | Seite 52 | «Entwick lung des Schengen-Besitzstands über die Erfüllung der verfassungsrechtlichen Vor aussetzungen in Bezug auf den N…»
Einschätzung: mehrdeutig, 2024-11-24_de#3, 2020-02-09_de#1, 2024-11-24_de#2 liegen gleichauf. Lässt die Behauptung nicht erkennen, welche gemeint ist, beende mit rueckfrage und diesen Optionen."""

VOTES_SUBLET = """Gefundene Vorlagen, beste zuerst. Score = ähnlichste Textstelle der Vorlage, Treffer = Textstellen der Vorlage unter den 50 ähnlichsten.
1. 2024-11-24_de#2 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Untermiete) | Score 0.908 | Treffer 20 | Seite 26 | «26 Zweite Vorlage: Untermiete Die neue Regelung soll solche Missbräuche bei der Untermiete verhindern. Künftig müssen Mi…»
2. 2024-11-24_de#3 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) | Score 0.861 | Treffer 15 | Seite 41 | «41 Auch an den Verfahrensrechten von Mieterinnen und Mietern ändert die Vorlage nichts. Die Mieterinnen und Mieter könne…»
3. 2020-02-09_de#1 | 09.02.2020 | Volksinitiativ e «Mehr bezahlbare Wohnungen» | Score 0.844 | Treffer 3 | Seite 10 | «Mietwohnungen führen. Subventionen des Bundes und der Kantone für energetische Erneuerungen sollen deshalb nur noch gewä…»
4. 2021-09-26_de#2 | 26.09.2021 | Ehe für alle | Score 0.843 | Treffer 2 | Seite 33 | «§ 33 4 Die entsprechenden Verordnungen sehen vor, dass Ehepaare, die dies wünschen, auf Dokumenten, Urkunden und Formula…»
5. 2022-05-15_de#2 | 15.05.2022 | Änderung des Transplantationsgesetzes (indirekter Gegenvorschlag zur Volksinitiative «Organspende fördern – Leben retten») | Score 0.839 | Treffer 2 | Seite 37 | «§ 37 nis der Zustimmung auch für die Entnahme von Organen, Geweben oder Zellen vorsehen, die nicht nach dem 4. Abschnitt…»
Einschätzung: 2024-11-24_de#2 passt klar am besten."""

PASSAGES_SUBLET = """Passagen aus 2024-11-24_de#2 (bewährte Suche mit der Behauptung): 8 gefunden, 5 neu im Pool. Pool dieser Vorlage: 5 Passagen; geprüft werden höchstens 10. 3 Passagen von anderen Vorlagen wurden nicht aufgenommen.
1. Seite 6, neu: «6 Zweite Vorlage: Untermiete Änderung des Obligationenrechts (Mietrecht: Untermiete) Mieterinnen und Mieter dürfen ihre Wohnung oder einzelne Zimmer vorübergehend untervermieten. Das Gleiche gilt für…»
2. Seite 1, andere Vorlage, nicht aufgenommen: «Änderung des Bundesgesetzes über die Krankenversicherung (KVG) (Einheitliche Finanzierung der Leistungen) Vierte Vorlage Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) Dritt…»
3. Seite 72 (Voting text (legal text)), andere Vorlage, nicht aufgenommen: «Ja Änderung des Bundesgesetzes über die Krankenversicherung (KVG) (Einheitliche Finanzierung der Leistungen) Ja Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) Ja Änderung de…»
4. Seite 7, neu: «7 Nein Gemäss Referendumskomitee schränkt die Vorlage die bewährte Untermiete massiv ein. Diese Schikane treffe Hunderttausende und sei Teil eines grossen Angriffs auf den Mieterschutz. Künftig drohe…»
5. Seite 31 (Arguments of the Federal Council and Parliament), neu: «31 Vermieterinnen und Vermieter dürfen ihren Mieterinnen und Mietern im Mietvertrag oder im Einzelfall weiterhin zusätzliche Möglichkeiten der Untermiete einräumen. Auf diese Weise können sie die jewe…»
6. Seite 3, andere Vorlage, nicht aufgenommen: «3 Vierte Vorlage Dritte Vorlage Zweite Vorlage Erste Vorlage Bundesbeschluss über den Ausbauschritt 2023 f ür die Nationalstrassen In Kürze 4 – 5 Im Detail 12 Argumente 18 Abstimmungstext 22 Änderung…»
7. Seite 24 (Official explanation in detail), neu: «24 Zweite Vorlage: Untermiete Im Detail Änderung des Obligationenrechts (Mietrecht: Untermiete) Im Detail Vorlage im Detail Argumente Referendumskomitee 28 Argumente Bundesrat und Parlament 30 Abstimm…»
8. Seite 32 (Voting text (legal text)), neu: «32 Abstimmungstext Obligationenrecht (Mietrecht: Untermiete) Änderung vom 29. September 2023 Zweite Vorlage: Untermiete Abstimmungstext Abstimmungstext Abstimmungstext 2 Abstimmungstext Obligationenre…»"""

SITUATIONS: Tuple[Situation, ...] = (
    Situation("a_neue_behauptung", "Neue Behauptung, noch keine Suche", CLAIM_AMBIGUOUS, "suche_vorlagen"),
    Situation("b_zwei_gleich_gute_vorlagen", "Beobachtung: zwei gleich gute Mietrecht-Vorlagen", CLAIM_AMBIGUOUS,
              "rueckfrage",
              (("suche_vorlagen", {"anfrage": CLAIM_AMBIGUOUS}, Observation("suche_vorlagen", VOTES_AMBIGUOUS)),)),
    Situation("c_passagen_gefunden", "Beobachtung: Passagen der richtigen Vorlage gefunden", CLAIM_SUBLET, "pruefen",
              (("suche_vorlagen", {"anfrage": CLAIM_SUBLET}, Observation("suche_vorlagen", VOTES_SUBLET)),
               ("suche_passagen", {"vorlage_id": SUBLET_VOTE}, Observation("suche_passagen", PASSAGES_SUBLET)))),
)
SITUATION_A, SITUATION_B, SITUATION_C = SITUATIONS
PLAN: Tuple[Tuple[str, Situation], ...] = (("json", SITUATION_A), ("native", SITUATION_A), ("native", SITUATION_B),
                                           ("native", SITUATION_C))
assert len(PLAN) == MAX_REQUESTS


class _Refused(Exception):
    """A start that was refused or failed before any request; the (German) message goes to stderr."""


class RecordingTransport(httpx.BaseTransport):
    """Wraps the real transport and keeps, per request, only the shape of the body and the HTTP status.

    Never stored: body text, messages, headers. `tool_choice` is kept when it is a plain string (else a marker), so a
    structured value cannot carry text into the record.
    """

    def __init__(self, inner: httpx.BaseTransport):
        self._inner = inner
        self.records: List[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        record = self._describe(request)
        self.records.append(record)
        response = self._inner.handle_request(request)  # an exception leaves `http_status` at None
        record["http_status"] = response.status_code
        return response

    @staticmethod
    def _describe(request: httpx.Request) -> dict:
        try:
            body = json.loads(request.read())
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return {"body_keys": None, "tool_choice": None, "max_completion_tokens": None, "http_status": None}
        choice, cap = body.get("tool_choice"), body.get("max_completion_tokens")
        return {"body_keys": sorted(body),
                "tool_choice": choice if choice is None or isinstance(choice, str) else "<kein Text>",
                "max_completion_tokens": cap if cap is None or type(cap) is int else "<keine Zahl>",
                "http_status": None}

    def close(self) -> None:
        self._inner.close()


def completion_cap_verdict(row: dict) -> str:
    """"angenommen" at HTTP 200 with a completion within the cap, "abgelehnt" at HTTP 400, else "unklar"."""
    status = row.get("http_status")
    if status == 200 and row.get("tokens_completion", 0) <= settings.LLM_MAX_TOKENS:
        return "angenommen"
    if status == 400:
        return "abgelehnt"
    return "unklar"


def decide(rows: List[dict]) -> dict:
    """The plan's decision rule over the native rows (skipped and missing ones count as failures)."""
    native = [row for row in rows if row["mode"] == "native"]
    of = sum(1 for mode, _ in PLAN if mode == "native")
    accepted = sum(1 for r in native if r.get("http_status") == 200 and r.get("tool_choice") == "required")
    valid = sum(1 for r in native if r.get("valid_action"))
    expected = sum(1 for r in native if r.get("expected_tool_chosen"))
    ok = len(native) == of and accepted == valid == expected == of
    return {"decision": "native" if ok else "json", "rule": RULE, "native_accepted": accepted, "native_valid": valid,
            "native_expected_tool": expected, "of": of}


def _skipped(situation: Situation, mode: str, reason: str) -> dict:
    return {"situation": situation.id, "mode": mode, "expected_tool": situation.expected_tool, "skipped": True,
            "tool": None, "valid_action": False, "expected_tool_chosen": False, "tokens_prompt": 0,
            "tokens_completion": 0, "http_status": None, "tool_choice": None, "max_completion_tokens": None,
            "body_keys": None, "error": f"übersprungen: {reason}"}


def run_smoke(chat_model: Any, recorder: RecordingTransport, secret: Optional[str], rows: List[dict]) -> Optional[str]:
    """The four requests in order, appending one row per request as it finishes (a crash keeps the finished ones).
    Returns None, or the reason the run stopped early; the remaining cells are rows with `skipped`."""
    deciders = {"native": NativeToolDecider(chat_model), "json": JsonActionDecider(chat_model)}
    stopped = None
    for mode, situation in PLAN:
        if stopped is not None:
            rows.append(_skipped(situation, mode, stopped))
            continue
        before = len(recorder.records)
        row, stopped = run_one(deciders[mode], mode, situation, secret)
        record = recorder.records[-1] if len(recorder.records) > before else {}
        row.update(http_status=record.get("http_status"), tool_choice=record.get("tool_choice"),
                   max_completion_tokens=record.get("max_completion_tokens"), body_keys=record.get("body_keys"))
        if not rows and row["http_status"] == 400:  # request 1 refused: the endpoint does not take the request shape
            stopped = f"HTTP 400 bei Anfrage 1: {row['error']}"
        rows.append(row)
        print(f"{situation.id} {mode}: {row['tool'] or 'kein Werkzeug'} "
              f"({'gültig' if row['valid_action'] else 'ungültig'}, erwartet {situation.expected_tool}: "
              f"{'ja' if row['expected_tool_chosen'] else 'nein'}), HTTP {row['http_status']}, "
              f"{row['tokens_prompt']}+{row['tokens_completion']} Tokens, {row['latency_ms']} ms"
              + (f", Fehler: {row['error']}" if row["error"] else ""))
    return stopped


def _display(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def build_report(rows: List[dict], budget: RequestBudget, journal: Path, stopped: Optional[str]) -> dict:
    decision = decide(rows)
    return {
        "kind": "real_apertus_native_tool_calling_smoke_not_quality_study",
        "complete": stopped is None and len(rows) == MAX_REQUESTS,
        "stopped_early": stopped,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.LLM_NAME,
        "endpoint_host": urlparse(config.LLM_BASE_URL).hostname,
        "temperature": 0,
        "max_requests": MAX_REQUESTS,
        "requests_used": budget.used,
        "budget_exceeded": budget.exceeded,
        "llm_max_tokens": settings.LLM_MAX_TOKENS,
        "journal": _display(journal),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prompt": "prompts.system_prompt() of the agent graph; request 1 appends decider.format_instruction(TOOL_SPECS), "
                  "requests 2-4 pass all TOOL_SPECS to bind_tools(tool_choice='required')",
        "history_source": "Observations are real stage-3 AgentTools outputs over the real source index (offline, mock "
                          "engine), hard-coded; suche_passagen without anfrage",
        "situations": [{"id": s.id, "description": s.description, "claim": s.claim, "expected_tool": s.expected_tool,
                        "history_tools": [tool for tool, _, _ in s.history]} for s in SITUATIONS],
        "rows": rows,
        "decision": decision,
        "recommended_tool_mode": decision["decision"],
        "completion_cap": {"verdict": completion_cap_verdict(rows[0]) if rows else "unklar",
                           "basis": "Anfrage 1 (JSON, Situation a): HTTP 200 und tokens_completion <= "
                                    f"{settings.LLM_MAX_TOKENS} = angenommen; HTTP 400 = abgelehnt; sonst unklar"},
        "limitations": [
            "One sample per request at temperature 0: a reliability smoke, not a quality measurement.",
            "The rule needs all three native requests accepted, valid and expected; anything less keeps json.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Bezahlter, gedeckelter Rauchtest: natives Tool-Calling mit tool_choice=required und Obergrenze "
                    f"der Antwortlänge. Höchstens {MAX_REQUESTS} HTTP-Anfragen.")
    parser.add_argument("--confirm-paid", action="store_true",
                        help=f"Bestätigt, dass bis zu {MAX_REQUESTS} bezahlte Anfragen gesendet werden dürfen.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, metavar="JSON",
                        help="Ergebnisdatei; das Anfrage-Journal liegt daneben (*.journal.jsonl). Beide werden nie "
                             f"überschrieben (Standard: {_display(DEFAULT_OUTPUT)}).")
    return parser


def _env_file() -> Optional[str]:
    return os.environ.get("FACT_ATTACK_ENV_FILE")


def _require_paid_run(args: argparse.Namespace, journal: Path) -> None:
    if not args.confirm_paid:
        raise _Refused(f"Dieser Rauchtest sendet bis zu {MAX_REQUESTS} bezahlte Anfragen an Apertus. Ohne "
                       "--confirm-paid wird nichts gesendet.")
    if not _env_file():
        raise _Refused("FACT_ATTACK_ENV_FILE fehlt (Pfad zur .env-Datei mit dem API-Schlüssel). Es wurde nichts "
                       "gesendet.")
    if not config.LLM_API_KEY:
        raise _Refused("Der API-Schlüssel fehlt (LLM_API_KEY oder API_KEY in der Datei aus FACT_ATTACK_ENV_FILE). "
                       "Es wurde nichts gesendet.")
    if args.output.exists() or journal.exists():
        raise _Refused(f"{args.output} oder {journal} existiert schon und wird nicht überschrieben: ein zweiter "
                       "bezahlter Lauf braucht eine bewusste Entscheidung. Es wurde nichts gesendet.")


def execute(output: Path, journal: Path, inner: Optional[httpx.BaseTransport] = None) -> int:
    """The four requests over `inner` (the real network transport unless a test passes a mock), then the report."""
    budget = RequestBudget(limit=MAX_REQUESTS, journal_path=journal)  # one budget for all four requests
    recorder = RecordingTransport(inner if inner is not None else httpx.HTTPTransport())
    chat_model = make_chat_model(budget, transport=recorder)
    rows: List[dict] = []
    stopped = None
    try:
        stopped = run_smoke(chat_model, recorder, config.LLM_API_KEY, rows)
    finally:  # whatever happened, the finished runs and the request count are kept
        while len(rows) < MAX_REQUESTS:  # a crash: the unsent requests are skipped, not missing
            mode, situation = PLAN[len(rows)]
            rows.append(_skipped(situation, mode, stopped or "Lauf abgebrochen"))
        report = build_report(rows, budget, journal, stopped)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decision = report["decision"]
    print(f"\nAnfragen: {budget.used} von {budget.limit} (Journal: {journal})\n"
          f"Regel: {decision['rule']}\n"
          f"Entscheidung: {decision['decision']} (angenommen {decision['native_accepted']}/{decision['of']}, "
          f"gültig {decision['native_valid']}/{decision['of']}, erwartet {decision['native_expected_tool']}/"
          f"{decision['of']})\nmax_completion_tokens: {report['completion_cap']['verdict']}\nErgebnis: {output}")
    if stopped is not None:
        print(f"Abgebrochen: {stopped}", file=sys.stderr)
        return EXIT_STOPPED
    return EXIT_DONE


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.output = args.output.expanduser()
        journal = args.output.with_suffix(".journal.jsonl")
        _require_paid_run(args, journal)
        return execute(args.output, journal)
    except _Refused as stop:
        print(stop, file=sys.stderr)
    except Exception as exc:
        print(f"Fehler: {type(exc).__name__}: {_scrub(str(exc), config.LLM_API_KEY)}", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
