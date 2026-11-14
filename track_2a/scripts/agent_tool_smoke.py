"""Capped real smoke of Apertus tool calling: does the model choose the agent's tools reliably, natively and as JSON?

Three fixed situations of the search loop, each decided once by the native decider (`bind_tools`) and once by the JSON
decider: 6 runs = 6 HTTP requests, counted on ONE `RequestBudget(limit=6)` with a journal (fsync before every send, SDK
retries off). The prompt is the graph's real one (`prompts.system_prompt`), the tools are the real `TOOL_SPECS`.

* (a) a new claim, no history            -> `suche_vorlagen` expected
* (b) two equally good Mietrecht votes   -> `rueckfrage` expected
* (c) passages found for the right vote  -> `pruefen` expected

This is a reliability smoke of the tool format, not a quality study: one sample per cell, temperature 0.
Decision rule (verbatim from the plan): `native` if all 3 native runs give a valid tool, else `json` if all 3 JSON runs
are valid, else the limitation is documented and the default stays `json`. "Valid" means an offered tool with valid
arguments; whether it is also the expected tool is reported next to it, but is not part of the rule.

The observations in the histories are the real outputs of `AgentTools` over the real source index (offline, multilingual
E5, no LLM call, generated on 2026-10-31) and are hard-coded here, so the smoke needs neither index nor model.

The run is paid and starts only with `--confirm-paid` and `FACT_ATTACK_ENV_FILE` (the .env with the API key, read by
`src.config`; its content never goes into the report). The result file and the journal are never overwritten: a second
paid run needs a decision, not a repeated command.

Usage (from track_2a/):
  FACT_ATTACK_ENV_FILE=<path to .env> ../.venv312/bin/python scripts/agent_tool_smoke.py --confirm-paid \
      --output results/genai/agent_tool_smoke.json
Exit code: 0 all six runs were made, 1 refused (nothing sent) or failed, 2 stopped early (key or endpoint rejected).
"""
import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.agent.budget import RequestBudget
from src.agent.decider import AgentAction, JsonActionDecider, NativeToolDecider, make_chat_model
from src.agent.prompts import system_prompt
from src.agent.tools import TOOL_SPECS, Observation

MAX_REQUESTS = 6
MODES = ("native", "json")
RAW_CHARS = 600  # cap on the model text kept in a row
ERROR_CHARS = 300
FATAL_STATUS = (401, 403, 404)  # key, permission or model/endpoint rejected: the other runs would fail the same way
EXIT_DONE, EXIT_REFUSED, EXIT_STOPPED = 0, 1, 2
DEFAULT_OUTPUT = ROOT / "results" / "genai" / "agent_tool_smoke.json"
RULE = ("native, wenn alle 3 nativen Läufe ein gültiges Werkzeug liefern; sonst json, wenn alle 3 JSON-Läufe gültig "
        "sind; sonst wird die Einschränkung dokumentiert und der Standard bleibt json.")

CLAIM_AMBIGUOUS = "Die Mietrechtsvorlage stärkt die Vermieter."
CLAIM_SUBLET = ("Bei der Untermiete müssen Mieterinnen und Mieter künftig die schriftliche Zustimmung der "
                "Vermieterin oder des Vermieters einholen.")
SUBLET_VOTE = "2024-11-24_de#2"

# suche_vorlagen for CLAIM_AMBIGUOUS: Eigenbedarf (#3, 0.867, 15 hits) and Untermiete (#2, 0.860, 14 hits) are too close
VOTES_AMBIGUOUS = """\
Gefundene Vorlagen, beste zuerst. Score = ähnlichste Textstelle der Vorlage, Treffer = Textstellen der Vorlage unter den 50 ähnlichsten. Die Scores liegen bei diesem Modell eng beieinander; die Trefferzahl trennt oft besser.
1. 2024-11-24_de#3 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) | Score 0.867 | Treffer 15 | Seite 40 | «40 Dritte Vorlage: Kündigung wegen Eigenbedarfs Argumente Bundesrat und Parlament Eigentümerinnen und Eigentümer sollen…»
2. 2020-02-09_de#1 | 09.02.2020 | Volksinitiativ e «Mehr bezahlbare Wohnungen» | Score 0.860 | Treffer 13 | Seite 13 | «13 Die Initiative verlangt, dass mehr Areale für gemein nützige Wohnungen zur Verfügung stehen. Dafür braucht es vor all…»
3. 2024-11-24_de#2 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Untermiete) | Score 0.860 | Treffer 14 | Seite 29 | «29 Der Text auf dieser Doppelseite stammt vom Referendumskomitee. Es ist für den Inhalt und die Wortwahl verantwortlich.…»
4. 2021-11-28_de#3 | 28.11.2021 | Änderung vom 19. März 2021 des Covid-19-Gesetzes | Score 0.848 | Treffer 1 | Seite 36 | «beschlossene Gesetz setzt dem Bundesrat klare Leitplanken, ermöglicht die ge meinsame Krisenbewältigung und sichert die…»
5. 2022-05-15_de#3 | 15.05.2022 | Übernahme der EU-Verordnung über die Europäische Grenz- und Küstenwache (Weiterentwicklung des Schengen-Besitzstands) | Score 0.845 | Treffer 3 | Seite 52 | «Entwick lung des Schengen-Besitzstands über die Erfüllung der verfassungsrechtlichen Vor aussetzungen in Bezug auf den N…»"""

# suche_vorlagen for CLAIM_SUBLET: Untermiete (#2) leads clearly
VOTES_SUBLET = """\
Gefundene Vorlagen, beste zuerst. Score = ähnlichste Textstelle der Vorlage, Treffer = Textstellen der Vorlage unter den 50 ähnlichsten. Die Scores liegen bei diesem Modell eng beieinander; die Trefferzahl trennt oft besser.
1. 2024-11-24_de#2 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Untermiete) | Score 0.908 | Treffer 20 | Seite 26 | «26 Zweite Vorlage: Untermiete Die neue Regelung soll solche Missbräuche bei der Untermiete verhindern. Künftig müssen Mi…»
2. 2024-11-24_de#3 | 24.11.2024 | Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) | Score 0.861 | Treffer 15 | Seite 41 | «41 Auch an den Verfahrensrechten von Mieterinnen und Mietern ändert die Vorlage nichts. Die Mieterinnen und Mieter könne…»
3. 2020-02-09_de#1 | 09.02.2020 | Volksinitiativ e «Mehr bezahlbare Wohnungen» | Score 0.844 | Treffer 3 | Seite 10 | «Mietwohnungen führen. Subventionen des Bundes und der Kantone für energetische Erneuerungen sollen deshalb nur noch gewä…»
4. 2021-09-26_de#2 | 26.09.2021 | Ehe für alle | Score 0.843 | Treffer 2 | Seite 33 | «§ 33 4 Die entsprechenden Verordnungen sehen vor, dass Ehepaare, die dies wünschen, auf Dokumenten, Urkunden und Formularen als Ehemann und Ehefrau aufgeführt werden beziehungsweise als Vater und Mutt…»
5. 2022-05-15_de#2 | 15.05.2022 | Änderung des Transplantationsgesetzes (indirekter Gegenvorschlag zur Volksinitiative «Organspende fördern – Leben retten») | Score 0.839 | Treffer 2 | Seite 37 | «§ 37 nis der Zustimmung auch für die Entnahme von Organen, Geweben oder Zellen vorsehen, die nicht nach dem 4. Abschnitt…»"""

# suche_passagen(SUBLET_VOTE, CLAIM_SUBLET): the proven search, 8 passages, including two weak ones (cover, contents)
PASSAGES_SUBLET = """\
Passagen aus 2024-11-24_de#2 (bewährte Suche): 8 gefunden, 8 neu im Pool. Pool dieser Vorlage: 8 von höchstens 10 Passagen.
1. Seite 6, neu: «6 Zweite Vorlage: Untermiete Änderung des Obligationenrechts (Mietrecht: Untermiete) Mieterinnen und Mieter dürfen ihre Wohnung oder einzelne Zimmer vorübergehend untervermieten. Das Gleiche gilt für…»
2. Seite 1, neu: «Änderung des Bundesgesetzes über die Krankenversicherung (KVG) (Einheitliche Finanzierung der Leistungen) Vierte Vorlage Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) Dritt…»
3. Seite 72 (Voting text (legal text)), neu: «Ja Änderung des Bundesgesetzes über die Krankenversicherung (KVG) (Einheitliche Finanzierung der Leistungen) Ja Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs) Ja Änderung de…»
4. Seite 7, neu: «7 Nein Gemäss Referendumskomitee schränkt die Vorlage die bewährte Untermiete massiv ein. Diese Schikane treffe Hunderttausende und sei Teil eines grossen Angriffs auf den Mieterschutz. Künftig drohe…»
5. Seite 31 (Arguments of the Federal Council and Parliament), neu: «31 Vermieterinnen und Vermieter dürfen ihren Mieterinnen und Mietern im Mietvertrag oder im Einzelfall weiterhin zusätzliche Möglichkeiten der Untermiete einräumen. Auf diese Weise können sie die jewe…»
6. Seite 3, neu: «3 Vierte Vorlage Dritte Vorlage Zweite Vorlage Erste Vorlage Bundesbeschluss über den Ausbauschritt 2023 f ür die Nationalstrassen In Kürze 4 – 5 Im Detail 12 Argumente 18 Abstimmungstext 22 Änderung…»
7. Seite 24 (Official explanation in detail), neu: «24 Zweite Vorlage: Untermiete Im Detail Änderung des Obligationenrechts (Mietrecht: Untermiete) Im Detail Vorlage im Detail Argumente Referendumskomitee 28 Argumente Bundesrat und Parlament 30 Abstimm…»
8. Seite 32 (Voting text (legal text)), neu: «32 Abstimmungstext Obligationenrecht (Mietrecht: Untermiete) Änderung vom 29. September 2023 Zweite Vorlage: Untermiete Abstimmungstext Abstimmungstext Abstimmungstext 2 Abstimmungstext Obligationenre…»"""


@dataclass(frozen=True)
class Situation:
    id: str
    description: str
    claim: str
    expected_tool: str
    history: Tuple[Tuple[str, dict, Observation], ...] = ()  # (tool, args, observation) already seen


SITUATIONS: Tuple[Situation, ...] = (
    Situation("a_neue_behauptung", "Neue Behauptung, noch keine Suche", CLAIM_AMBIGUOUS, "suche_vorlagen"),
    Situation("b_zwei_gleich_gute_vorlagen", "Beobachtung: zwei gleich gute Mietrecht-Vorlagen", CLAIM_AMBIGUOUS,
              "rueckfrage",
              (("suche_vorlagen", {"anfrage": CLAIM_AMBIGUOUS}, Observation("suche_vorlagen", VOTES_AMBIGUOUS)),)),
    Situation("c_passagen_gefunden", "Beobachtung: Passagen der richtigen Vorlage gefunden", CLAIM_SUBLET, "pruefen",
              (("suche_vorlagen", {"anfrage": CLAIM_SUBLET}, Observation("suche_vorlagen", VOTES_SUBLET)),
               ("suche_passagen", {"vorlage_id": SUBLET_VOTE, "anfrage": CLAIM_SUBLET},
                Observation("suche_passagen", PASSAGES_SUBLET)))),
)


class _Refused(Exception):
    """A start that was refused or failed before any request; the (German) message goes to stderr."""


def build_messages(situation: Situation, decider: Any) -> List[dict]:
    """The conversation as the graph builds it: system prompt, the claim as user message, then each earlier action and
    observation as the decider itself records it (tool_calls + tool message, or assistant + user text)."""
    messages = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": situation.claim}]
    for number, (tool, args, observation) in enumerate(situation.history, 1):
        raw = json.dumps({"tool": tool, "args": args}, ensure_ascii=False)
        messages += decider.observation_messages(AgentAction(tool, args, raw, call_id=f"call_{number}"), observation)
    return messages


def _scrub(text: str, secret: Optional[str]) -> str:
    """`text` without the API key: error messages of an endpoint may echo a (partly masked) key."""
    if secret:
        for part in (secret, secret[:8]):
            if len(part) >= 4:
                text = text.replace(part, "[Schlüssel entfernt]")
    return text


def _named_tool(action: AgentAction) -> Optional[str]:
    """The tool the model named: the validated one, or the name in an invalid action's raw JSON."""
    if action.tool is not None:
        return action.tool
    try:
        value, _ = json.JSONDecoder().raw_decode(action.raw)
    except ValueError:
        return None
    name = value.get("tool") if isinstance(value, dict) else None
    return name[:60] if isinstance(name, str) else None


def _row(situation: Situation, mode: str, **fields: Any) -> dict:
    row = {"situation": situation.id, "mode": mode, "expected_tool": situation.expected_tool, "tool": None,
           "tool_chosen": False, "args_valid": False, "valid_action": False, "expected_tool_chosen": False,
           "tokens_prompt": 0, "tokens_completion": 0, "latency_ms": None, "error": None, "raw": ""}
    row.update(fields)
    return row


def _is_fatal(error: Exception) -> Optional[str]:
    """Why the run should stop after this exception (a rejected key or endpoint, an unreachable host), else None."""
    status = getattr(error, "status_code", None)
    if status in FATAL_STATUS:
        return f"HTTP {status}"
    cause = error.__cause__
    if cause is not None and type(cause).__name__ in ("ConnectError", "ConnectTimeout"):
        return type(cause).__name__
    return None


def run_one(decider: Any, mode: str, situation: Situation, secret: Optional[str]) -> Tuple[dict, Optional[str]]:
    """One decision, one request. (row, reason to stop): an exception of the chat model becomes the row's error."""
    messages = build_messages(situation, decider)
    started = time.perf_counter()
    try:
        action = decider.decide(messages, copy.deepcopy(TOOL_SPECS), [])
    except Exception as exc:  # timeout, HTTP error, spent budget: the run is a failed cell, not a crash
        error = _scrub(f"{type(exc).__name__}: {str(exc)}", secret)[:ERROR_CHARS]
        row = _row(situation, mode, error=error, latency_ms=round((time.perf_counter() - started) * 1000, 1))
        reason = _is_fatal(exc)
        return row, (_scrub(f"{reason}: {error}", secret) if reason else None)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    tool = _named_tool(action)
    row = _row(situation, mode, tool=tool, tool_chosen=tool is not None, args_valid=action.tool is not None,
               valid_action=action.tool is not None, expected_tool_chosen=action.tool == situation.expected_tool,
               tokens_prompt=action.tokens_prompt, tokens_completion=action.tokens_completion, latency_ms=latency_ms,
               error=action.error, raw=_scrub(action.raw, secret)[:RAW_CHARS])
    if action.tool is not None:
        row["args"] = action.args
    return row, None


def run_smoke(budget: RequestBudget, chat_model: Any, secret: Optional[str], rows: List[dict]) -> Optional[str]:
    """All situations x both modes in order, appending one row per cell to `rows` as it finishes (so a crash keeps the
    finished ones). Returns None, or the reason the run stopped early; the remaining cells are rows with `skipped`."""
    deciders = {"native": NativeToolDecider(chat_model), "json": JsonActionDecider(chat_model)}
    stopped = None
    for situation in SITUATIONS:
        for mode in MODES:
            if stopped is not None:
                rows.append(_row(situation, mode, skipped=True, error=f"übersprungen: {stopped}"))
                continue
            row, stopped = run_one(deciders[mode], mode, situation, secret)
            rows.append(row)
            print(f"{situation.id} {mode}: {row['tool'] or 'kein Werkzeug'} "
                  f"({'gültig' if row['valid_action'] else 'ungültig'}, erwartet {situation.expected_tool}: "
                  f"{'ja' if row['expected_tool_chosen'] else 'nein'}), "
                  f"{row['tokens_prompt']}+{row['tokens_completion']} Tokens, {row['latency_ms']} ms"
                  + (f", Fehler: {row['error']}" if row["error"] else ""))
    return stopped


def decide_tool_mode(rows: List[dict]) -> dict:
    """The plan's decision rule. A mode is fully valid only with exactly one valid run per situation."""
    count = {mode: sum(1 for r in rows if r["mode"] == mode and r["valid_action"]) for mode in MODES}
    expected = {mode: sum(1 for r in rows if r["mode"] == mode and r.get("expected_tool_chosen")) for mode in MODES}
    needed = len(SITUATIONS)
    decision = "native" if count["native"] == needed else "json" if count["json"] == needed else "neither"
    return {"decision": decision, "rule": RULE, "native_valid": count["native"], "json_valid": count["json"],
            "native_expected_tool": expected["native"], "json_expected_tool": expected["json"], "of": needed}


def recommended_tool_mode(decision: dict) -> str:
    """The value for `settings.TOOL_MODE`: the decided mode; with neither, the default `json` stays."""
    return decision["decision"] if decision["decision"] in MODES else "json"


def _display(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def build_report(rows: List[dict], budget: RequestBudget, journal: Path, stopped: Optional[str]) -> dict:
    decision = decide_tool_mode(rows)
    return {
        "kind": "real_apertus_tool_calling_smoke_not_quality_study",
        "complete": stopped is None and len(rows) == len(SITUATIONS) * len(MODES),
        "stopped_early": stopped,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.LLM_NAME,
        "endpoint_host": urlparse(config.LLM_BASE_URL).hostname,
        "temperature": 0,
        "max_requests": MAX_REQUESTS,
        "requests_used": budget.used,
        "budget_exceeded": budget.exceeded,
        "journal": _display(journal),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "prompt": "prompts.system_prompt() of the agent graph; JSON mode appends decider.format_instruction(TOOL_SPECS), "
                  "native mode passes TOOL_SPECS to bind_tools",
        "history_source": "Observations are real AgentTools outputs over the real source index (offline), hard-coded",
        "situations": [{"id": s.id, "description": s.description, "claim": s.claim, "expected_tool": s.expected_tool,
                        "history_tools": [tool for tool, _, _ in s.history]} for s in SITUATIONS],
        "rows": rows,
        "decision": decision,
        "recommended_tool_mode": recommended_tool_mode(decision),
        "limitations": [
            "One sample per situation and mode at temperature 0: a reliability smoke, not a quality measurement.",
            "The rule counts valid tools only; the expected tool is reported but does not decide.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Bezahlter, gedeckelter Rauchtest: wählt Apertus Werkzeuge nativ und als JSON-Aktion? "
                    f"Höchstens {MAX_REQUESTS} HTTP-Anfragen.")
    parser.add_argument("--confirm-paid", action="store_true",
                        help=f"Bestätigt, dass bis zu {MAX_REQUESTS} bezahlte Anfragen gesendet werden dürfen.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, metavar="JSON",
                        help="Ergebnisdatei; das Anfrage-Journal liegt daneben (*.journal.jsonl). Beide werden nie "
                             f"überschrieben (Standard: {_display(DEFAULT_OUTPUT)}).")
    return parser


def _require_paid_run(args: argparse.Namespace, journal: Path) -> None:
    if not args.confirm_paid:
        raise _Refused(f"Dieser Rauchtest sendet bis zu {MAX_REQUESTS} bezahlte Anfragen an Apertus. Ohne "
                       "--confirm-paid wird nichts gesendet.")
    if not os.environ.get("FACT_ATTACK_ENV_FILE"):
        raise _Refused("FACT_ATTACK_ENV_FILE fehlt (Pfad zur .env-Datei mit dem API-Schlüssel). Es wurde nichts "
                       "gesendet.")
    if not config.LLM_API_KEY:
        raise _Refused("Der API-Schlüssel fehlt (LLM_API_KEY oder API_KEY in der Datei aus FACT_ATTACK_ENV_FILE). "
                       "Es wurde nichts gesendet.")
    if args.output.exists() or journal.exists():
        raise _Refused(f"{args.output} oder {journal} existiert schon und wird nicht überschrieben: ein zweiter "
                       "bezahlter Lauf braucht eine bewusste Entscheidung. Es wurde nichts gesendet.")


def _run(args: argparse.Namespace) -> int:
    output = args.output.expanduser()
    journal = output.with_suffix(".journal.jsonl")
    args.output = output
    _require_paid_run(args, journal)
    budget = RequestBudget(limit=MAX_REQUESTS, journal_path=journal)  # one budget for all six runs
    chat_model = make_chat_model(budget)
    rows: List[dict] = []
    stopped = None
    try:
        stopped = run_smoke(budget, chat_model, config.LLM_API_KEY, rows)
    finally:  # whatever happened, the finished runs and the request count are kept
        report = build_report(rows, budget, journal, stopped)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decision = report["decision"]
    print(f"\nAnfragen: {budget.used} von {budget.limit} (Journal: {journal})\n"
          f"Entscheidung: {decision['decision']} (nativ gültig {decision['native_valid']}/{decision['of']}, "
          f"JSON gültig {decision['json_valid']}/{decision['of']}); empfohlener TOOL_MODE: "
          f"{report['recommended_tool_mode']}\nErgebnis: {output}")
    if stopped is not None:
        print(f"Abgebrochen nach einem Fehler, der alle weiteren Läufe betroffen hätte: {stopped}", file=sys.stderr)
        return EXIT_STOPPED
    return EXIT_DONE


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except _Refused as stop:
        print(stop, file=sys.stderr)
    except Exception as exc:
        print(f"Fehler: {type(exc).__name__}: {_scrub(str(exc), config.LLM_API_KEY)}", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
