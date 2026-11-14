"""Freeze the evaluation's configuration before the one-time test run.

Usage (from track_2a/):
  python scripts/freeze_config.py --out results/genai/config_freeze.json --dev-run DIR

Writes the freeze file once (an existing one is never overwritten), only on a clean tree (no change outside
track_2a/results/) and only after a real dev evaluation in DIR (`summary.json` of run_agent_eval.py: split dev, no mock,
clean tree, the same configuration hash). The file holds the configuration hash (`eval_runner.config_hash`: git tree
SHAs of src/ and scripts/, model, guard thresholds, TOOL_MODE, prompts.py, testset SHA, settings), its parts, the commit
SHA for information and the path of the dev run. `run_agent_eval.py --split test --freeze FILE` checks it. Sends no
request; the key file is never read here beyond what `src.config` loads, and nothing of it is printed.
"""
import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.agent import eval_runner

EXIT_OK, EXIT_REFUSED = 0, 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path, metavar="FILE",
                        help="Freeze-Datei (wird nie überschrieben).")
    parser.add_argument("--dev-run", required=True, type=Path, metavar="DIR",
                        help="Ergebnisordner der echten Dev-Evaluation (mit summary.json).")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        freeze = eval_runner.write_freeze(args.out, args.dev_run)
    except eval_runner.FreezeError as refused:
        print(f"Kein Freeze: {eval_runner.redact(refused)}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"Freeze geschrieben: {args.out} (config_hash {freeze['config_hash'][:12]}…, Dev-Lauf {args.dev_run})")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
