"""Command line of the agent, run from track_2a/:  python -m src.agent "<Behauptung>" [Optionen]

Wiring only: arguments, the offline or the real run, output and exit code. The agent itself is graph.py.

* `--mock`: the scripted decider and `ApertusClient(mock=True)`; no request leaves the machine.
* Without `--mock` the run is paid: it needs `FACT_ATTACK_ENV_FILE` and an API key, and `--max-requests` (at most 12)
  is a hard cap on the HTTP requests of the check, counted for the agent LLM and for the NLI client alike.

Exit code: 0 for a result (also `rueckfrage` and `quelle_fehlt`), 2 for `technischer_fehler`, 1 for a wrong call or a
setup that failed before any check ran (argparse's own usage exit code 2 is replaced to keep 2 unambiguous). The engine
and the index are closed even if one of them fails to close; an unexpected failure of a real run names its request
journal on stderr.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from src import config
from src.agent import settings
from src.agent.budget import RequestBudget, install_on_apertus
from src.agent.cli_format import format_result, format_steps, result_to_dict
from src.agent.decider import MODES, make_decider
from src.agent.search_backend import LocalBackend
from src.agent.source_index import SourceIndex
from src.agent.tools import AgentTools
from src.agent.trace import Tracer
from src.apertus_client import ApertusClient
from src.booklet_catalog import load_catalog
from src.inference import ClaimVerificationEngine

EXIT_RESULT, EXIT_USAGE, EXIT_TECHNICAL = 0, 1, 2


class _Abort(Exception):
    """A call or configuration problem; the message (German) goes to stderr and nothing was checked."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits with 2 here; 2 means technischer_fehler in this CLI
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: Fehler: {message}\n")


def _request_limit(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= settings.MAX_REQUESTS:
        raise argparse.ArgumentTypeError(
            f"muss eine ganze Zahl von 1 bis {settings.MAX_REQUESTS} sein (erhalten: {text!r})")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m src.agent", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Prüft eine Behauptung gegen die Abstimmungsbüchlein: Der Agent sucht die Vorlage und die "
                    "Textstellen, prüft sie und zeigt die Schritte.",
        epilog=f"Exit-Code: 0 Ergebnis (auch rueckfrage, quelle_fehlt), {EXIT_TECHNICAL} technischer_fehler, "
               f"{EXIT_USAGE} falscher Aufruf oder fehlgeschlagener Start.")
    parser.add_argument("claim", metavar="BEHAUPTUNG", help=f"Die Behauptung, 1 bis {settings.MAX_CLAIM_CHARS} Zeichen.")
    parser.add_argument("--quelle", metavar="VORLAGE_ID",
                        help="Vorlage fest vorgeben (zum Beispiel 2024-11-24_de#1): keine Suche nach der Vorlage.")
    parser.add_argument("--modus", choices=MODES,
                        help=f"Entscheider: native, json oder scripted (Standard: {settings.TOOL_MODE}).")
    parser.add_argument("--mock", action="store_true",
                        help="Ohne Netz: skriptgesteuerter Entscheider und Mock-Client, keine Anfrage.")
    parser.add_argument("--max-requests", type=_request_limit, default=settings.MAX_REQUESTS, metavar="N",
                        help=f"Harte Obergrenze der HTTP-Anfragen dieser Prüfung, 1 bis {settings.MAX_REQUESTS} "
                             f"(Standard: {settings.MAX_REQUESTS}).")
    parser.add_argument("--trace-dir", type=Path, default=settings.TRACE_DIR, metavar="DIR",
                        help=f"Ordner für Trace und Anfrage-Journal (Standard: {settings.TRACE_DIR}).")
    parser.add_argument("--index-dir", type=Path, default=settings.INDEX_DIR, metavar="DIR",
                        help=f"Ordner des Quellenindex (Standard: {settings.INDEX_DIR}).")
    parser.add_argument("--json", action="store_true", help="Das Ergebnis als JSON ausgeben, sonst nichts auf stdout.")
    return parser


def _require_real_run() -> None:
    if not os.environ.get("FACT_ATTACK_ENV_FILE"):
        raise _Abort("Ohne --mock wird FACT_ATTACK_ENV_FILE verlangt (Pfad zur .env-Datei mit dem API-Schlüssel), "
                     "weil ein echter Lauf bezahlte Anfragen sendet. Es wurde nichts gesendet. "
                     "Für einen Lauf ohne Netz: --mock.")
    if not config.LLM_API_KEY:
        raise _Abort("Der API-Schlüssel fehlt (LLM_API_KEY oder API_KEY in der Datei aus FACT_ATTACK_ENV_FILE). "
                     "Es wurde nichts gesendet. Für einen Lauf ohne Netz: --mock.")


def _run(args: argparse.Namespace) -> int:
    claim = args.claim
    if not claim.strip() or len(claim) > settings.MAX_CLAIM_CHARS:
        raise _Abort(f"Die Behauptung muss 1 bis {settings.MAX_CLAIM_CHARS} Zeichen lang sein (erhalten: {len(claim)}).")
    mode = args.modus or settings.TOOL_MODE
    if args.mock:
        if mode != "scripted" and args.modus is not None:
            raise _Abort(f"--mock läuft immer mit dem skriptgesteuerten Entscheider; --modus {args.modus} passt dazu "
                         "nicht (--modus scripted oder weglassen).")
        mode = "scripted"
    else:
        _require_real_run()
    try:
        from src.agent.graph import run_check
    except ImportError as exc:  # langgraph is optional
        raise _Abort(f"Die Agentenschicht ist nicht installiert ({exc}). "
                     "Bitte track_2a/requirements-agent.txt installieren.") from None
    trace_dir = args.trace_dir.expanduser()
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{os.getpid()}"
    budget: Optional[RequestBudget] = None
    try:
        with contextlib.ExitStack() as cleanup:  # closes the engine, then the index, even if a close fails
            index = SourceIndex(load_catalog(config.DATA_DIR / "booklet_catalog.json"), config.BOOKLETS_DIR,
                                index_dir=args.index_dir.expanduser())  # one process per local index
            cleanup.callback(index.close)
            engine = ClaimVerificationEngine(apertus_client=ApertusClient(mock=args.mock))
            cleanup.callback(engine.close)
            journal = None if args.mock else trace_dir / f"{run_id}.requests.jsonl"
            budget = RequestBudget(limit=args.max_requests, journal_path=journal)  # one budget per check
            if not args.mock:
                install_on_apertus(engine.client, budget)  # the NLI client; the agent LLM is counted in make_decider
            try:
                backend = LocalBackend(index, engine, config.BOOKLETS_DIR)  # the CLI closes index and engine itself
                tools = AgentTools(backend, config.BOOKLETS_DIR, fixed_vote_id=args.quelle)
            except ValueError as exc:
                raise _Abort(f"{exc}. Die Vorlagen-IDs stehen im Quellenindex (Form 2024-11-24_de#1).") from None
            decider = make_decider(mode, budget, fixed_vote_id=args.quelle)
            tracer = Tracer(trace_dir / f"{run_id}.trace.jsonl")
            result = run_check(claim, tools=tools, decider=decider, engine=engine, tracer=tracer, budget=budget)
        requests = (f"{budget.used} von {budget.limit} (Journal: {journal})" if journal
                    else "keine (Mock-Lauf ohne Netz)")
        footer = f"Anfragen: {requests}\nTrace: {tracer.jsonl_path}"
        if args.json:
            print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
            print(footer, file=sys.stderr)
        else:
            label = "Mock (skriptgesteuert, ohne Netz)" if args.mock else f"echt, Entscheider {mode}"
            print(f"Behauptung: {' '.join(claim.split())}\nModus: {label}\n\nSchritte:\n"
                  f"{format_steps(result.events)}\n\n{format_result(result)}\n\n{footer}")
    except _Abort:
        raise
    except Exception:
        if budget is not None and budget.journal_path is not None:  # a real run: requests may have been sent
            print(f"Anfragen bis zum Fehler: {budget.used} von {budget.limit} (Journal: {budget.journal_path})",
                  file=sys.stderr)
        raise
    return EXIT_TECHNICAL if result.outcome == "technischer_fehler" else EXIT_RESULT


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except _Abort as stop:
        print(stop, file=sys.stderr)
    except Exception as exc:  # setup or an unexpected failure: there is no result to show
        print(f"Fehler: {type(exc).__name__}: {exc}", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
