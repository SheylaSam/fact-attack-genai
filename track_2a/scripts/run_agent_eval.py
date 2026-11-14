"""Run one arm of the agent evaluation over a split, under a hard request cap.

Usage (from track_2a/):
  python scripts/run_agent_eval.py --arm {b0,b1,b2,regression-b0,regression-agent} --split {dev,test,regression}
                                   --max-requests N --out DIR
                                   [--resume] [--mock] [--freeze FILE]

* `--mock`: the scripted decider and the mock Apertus client; no request leaves the machine.
* Without `--mock` the run is paid: it needs `FACT_ATTACK_ENV_FILE` (never printed) and an API key, and
  `--max-requests` may not exceed the per-check cap (`settings.MAX_REQUESTS`) times the number of cases.
* `--max-requests` is the cap of the whole campaign; `--resume` continues a run in DIR and counts what it spent.
* `--split test` needs `--freeze` (scripts/freeze_config.py) and runs once per arm. The order is fixed: everything
  that can fail is checked first, then `require_freeze`, `mark_test_run` for the arm (synced to disk, with DIR) and
  only then the test cases are read. A second new run of the same arm is refused. After an abort the run of an arm is
  only resumed (`--resume`, same arm, same DIR, same configuration on a clean tree), never evaluated anew. A mock
  run on the real test split is refused (it would use up the arm's one test run); a mock rehearsal works on a
  synthetic test set only.
* `--split regression` is the 42 dev cases of `data/genai/ost_june_dev.jsonl` and goes with the two regression arms,
  `regression-b0` (the production path, vote None) and `regression-agent` (the agent with the case's source given);
  the two belong to this split alone. It is dev data: no freeze. Run both arms on the same day (the reference is a
  fresh B0 run) and compare them with `regression.compare_paired`.

Results in DIR: `cases.jsonl` (one record per case), `journals/` (one request journal per check), `traces/`,
`not_run.json`, `campaign.json`, `summary.json`. Exit code 0 when the campaign ran (also with technical errors or cases
not run), 1 when it was refused or failed.
"""
import argparse
import contextlib
import os
from pathlib import Path
import sys
from typing import Iterator, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.agent import eval_runner, settings
from src.agent import testset as testset_module
from src.agent.regression import REGRESSION_ARMS, load_regression_cases

EXIT_OK, EXIT_REFUSED = 0, 1
SPLITS = ("dev", "test", "regression")


class _Abort(Exception):
    """A call or configuration problem; the German message goes to stderr and nothing was run."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", required=True, choices=tuple(eval_runner.ARMS), help="Arm der Evaluation.")
    parser.add_argument("--split", required=True, choices=SPLITS, help="Welche Fälle laufen.")
    parser.add_argument("--max-requests", required=True, type=int, metavar="N",
                        help="Harte Obergrenze der HTTP-Anfragen der ganzen Kampagne.")
    parser.add_argument("--out", required=True, type=Path, metavar="DIR",
                        help="Ergebnisordner (wird nie überschrieben).")
    parser.add_argument("--resume", action="store_true", help="Einen abgebrochenen Lauf in DIR fortsetzen.")
    parser.add_argument("--mock", action="store_true", help="Ohne Netz: skriptgesteuerter Entscheider, Mock-Client.")
    parser.add_argument("--freeze", type=Path, metavar="FILE", help="Freeze-Datei; Pflicht für --split test.")
    return parser


@contextlib.contextmanager
def open_wiring(mock: bool, trace_dir: Path) -> Iterator["eval_runner.Wiring"]:
    """The shared parts of every check: the local source index (one process only) and the booklet catalog."""
    from src.agent.source_index import SourceIndex
    from src.booklet_catalog import available_sources, load_catalog
    catalog = load_catalog(config.DATA_DIR / "booklet_catalog.json")
    index = SourceIndex(catalog, config.BOOKLETS_DIR, index_dir=settings.INDEX_DIR)
    try:
        yield eval_runner.Wiring(index=index, booklets_dir=config.BOOKLETS_DIR, mock=mock,
                                 catalog=available_sources(config.BOOKLETS_DIR, catalog), trace_dir=trace_dir)
    finally:
        index.close()


def _require_real_run() -> None:
    if not os.environ.get("FACT_ATTACK_ENV_FILE"):
        raise _Abort("Ohne --mock wird FACT_ATTACK_ENV_FILE verlangt (Pfad zur .env-Datei mit dem API-Schlüssel), "
                     "weil ein echter Lauf bezahlte Anfragen sendet. Es wurde nichts gesendet.")
    if not config.LLM_API_KEY:
        raise _Abort("Der API-Schlüssel fehlt (LLM_API_KEY oder API_KEY in der Datei aus FACT_ATTACK_ENV_FILE). "
                     "Es wurde nichts gesendet.")


def _check_cap(args: argparse.Namespace, cases: int) -> None:
    if args.max_requests < 1:
        raise _Abort(f"--max-requests muss mindestens 1 sein (erhalten: {args.max_requests}).")
    if not args.mock and args.max_requests > settings.MAX_REQUESTS * cases:
        raise _Abort(f"--max-requests {args.max_requests} ist höher als {settings.MAX_REQUESTS} Anfragen je Prüfung "
                     f"mal {cases} Fälle ({settings.MAX_REQUESTS * cases}).")


def _run(args: argparse.Namespace) -> int:
    if (args.split == "regression") != (args.arm in REGRESSION_ARMS):
        raise _Abort("--split regression läuft nur mit den Armen regression-b0 und regression-agent, und diese "
                     "Arme nur mit --split regression.")
    if args.out.exists() and not args.resume:
        raise _Abort(f"{args.out} existiert schon. Ein bestehender Lauf wird nie überschrieben "
                     "(fortsetzen: --resume).")
    if args.split == "test":
        if args.freeze is None:
            raise _Abort("--split test verlangt --freeze (scripts/freeze_config.py).")
        if args.mock and Path(eval_runner.CASES_PATH).resolve() == testset_module.CASES_PATH.resolve():
            raise _Abort("--mock auf dem echten Testteil würde den einmaligen Testlauf verbrauchen. Probeläufe nur "
                         "auf einem synthetischen Testset.")
    if not args.mock:
        _require_real_run()

    if args.split == "test":
        expected = eval_runner.test_case_count()  # from the manifest: no test case is read before the freeze check
        _check_cap(args, expected)
        eval_runner.require_freeze(args.freeze, args.arm, resume_out=args.out if args.resume else None)
        with open_wiring(args.mock, args.out / eval_runner.TRACE_DIR) as wiring:
            build = eval_runner.make_arm(args.arm, wiring)
            cases = eval_runner.open_test_split(args.freeze, args.arm, out_dir=args.out, resume=args.resume)
            if len(cases) != expected:
                raise _Abort(f"Der Testteil hat {len(cases)} Fälle, das Manifest nennt {expected}.")
            runs = eval_runner.run_campaign(args.arm, cases, build_arm=build, max_requests=args.max_requests,
                                            out_dir=args.out, resume=args.resume, split=args.split)
    else:  # dev, or the regression set (dev data too: no freeze)
        cases = load_regression_cases() if args.split == "regression" else eval_runner.load_dev()
        _check_cap(args, len(cases))
        with open_wiring(args.mock, args.out / eval_runner.TRACE_DIR) as wiring:
            runs = eval_runner.run_campaign(args.arm, cases, build_arm=eval_runner.make_arm(args.arm, wiring),
                                            max_requests=args.max_requests, out_dir=args.out, resume=args.resume,
                                            split=args.split)
    summary = eval_runner.write_summary(args.out, arm=args.arm, split=args.split, mock=args.mock, runs=runs,
                                        max_requests=args.max_requests,
                                        freeze=args.freeze if args.split == "test" else None)
    print(f"Arm {args.arm}, Split {args.split}{' (Mock)' if args.mock else ''}: {summary['finished']} von "
          f"{summary['cases']} Fällen gelaufen, {len(summary['not_run'])} nicht gelaufen.")
    print(f"Anfragen: {summary['requests_spent']} von höchstens {args.max_requests}. Ergebnisse: {args.out}")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except _Abort as stop:
        print(eval_runner.redact(stop), file=sys.stderr)
    except eval_runner.FreezeError as refused:
        print(f"Abgelehnt: {eval_runner.redact(refused)}", file=sys.stderr)
    except FileExistsError as exists:
        print(eval_runner.redact(exists), file=sys.stderr)
    except Exception as exc:  # setup or an unexpected failure; the journals keep what was sent
        print(f"Fehler: {type(exc).__name__}: {eval_runner.redact(exc)}", file=sys.stderr)
        if args.out.is_dir():
            print(f"Bisher gesendete Anfragen stehen in {args.out / eval_runner.JOURNAL_DIR}; fortsetzen mit "
                  "--resume (Testteil: gleicher Arm, gleicher Ordner).", file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
