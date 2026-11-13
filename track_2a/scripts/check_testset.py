"""Check the testset's cases against the booklet PDFs, or print the pages of a vote to write cases from.

Usage (from track_2a/):
  python scripts/check_testset.py [--cases PATH] [--partial]   check every case; exit 0 only without problems
  python scripts/check_testset.py --show VOTE_ID               print the pages of a vote (e.g. 2026-11-29_de#2)

The check reads the cases (`data/genai/testset/cases.jsonl`) and validates each against the PDF text (quote verbatim
on its page, vote known and in its split, counts of the finished set; `--partial` skips the counts). Claims of the
earlier data sets (holdout, OST dev) may not be repeated. `--show` is the only source for drafting a case: the raw
page text, every page headed by its number and the booklet parts it belongs to. The engine uses the mock Apertus
client: no request leaves the machine.
"""
import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.agent.testset import CASES_PATH, FETCH_COMMAND, load_vote_catalog, read_cases, validate
from src.apertus_client import ApertusClient
from src.booklet_catalog import load_catalog
from src.inference import ClaimVerificationEngine

PREVIOUS_CLAIM_FILES = (config.DATA_DIR / "holdout_2026-09-27" / "claims.jsonl",
                        config.DATA_DIR / "genai" / "ost_june_dev.jsonl")
EXIT_OK, EXIT_PROBLEMS = 0, 1


def make_engine() -> ClaimVerificationEngine:
    return ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))


def load_previous_claims() -> List[str]:
    """The `claim` of every line of the earlier data sets; FileNotFoundError if one is missing (no silent skip)."""
    claims = []
    for path in PREVIOUS_CLAIM_FILES:
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                claims.append(json.loads(line)["claim"])
    return claims


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=CASES_PATH, metavar="PATH",
                        help=f"cases.jsonl (Standard: {CASES_PATH})")
    parser.add_argument("--partial", action="store_true",
                        help="Entwurf prüfen: ohne die Zählung des fertigen Testsets (Fälle, Klassen, Arten)")
    parser.add_argument("--show", metavar="VOTE_ID",
                        help="Die Seiten dieser Vorlage ausgeben (Rohtext), ohne Fälle zu prüfen")
    return parser


def show(vote_id: str, engine) -> int:
    sources = load_catalog(config.DATA_DIR / "booklet_catalog.json")
    source_id = vote_id.split("#", 1)[0]
    name = f"{source_id}.pdf"
    if name not in sources or not source_id.endswith("_de"):
        print(f"{vote_id}: unbekannte Vorlage (gebraucht wird ein deutsches Büchlein des Katalogs, "
              "Form 2026-11-29_de#2)", file=sys.stderr)
        return EXIT_PROBLEMS
    votes = load_vote_catalog({name: sources[name]}, config.BOOKLETS_DIR, engine)  # only this booklet is parsed
    if source_id in votes.missing:
        print(f"{vote_id}: Büchlein fehlt ({votes.missing[source_id]}): {FETCH_COMMAND} ausführen", file=sys.stderr)
        return EXIT_PROBLEMS
    if vote_id not in votes.votes:
        print(f"{vote_id}: unbekannte Vorlage (das Büchlein hat {len(votes.votes)} Vorlagen, "
              f"{', '.join(votes.votes)})", file=sys.stderr)
        return EXIT_PROBLEMS
    parts = votes.parts(vote_id)
    pdf = config.BOOKLETS_DIR / name
    for page in votes.votes[vote_id].pages:
        names = ", ".join(part for part, pages in parts.items() if page in pages)
        print(f"=== S. {page} ({names}) ===" if names else f"=== S. {page} ===")
        print(engine.source_page_text(pdf, page) or "(kein lesbarer Text)")
    return EXIT_OK


def check(args: argparse.Namespace, engine) -> int:
    cases = read_cases(args.cases)
    sources = load_catalog(config.DATA_DIR / "booklet_catalog.json")
    votes = load_vote_catalog(sources, config.BOOKLETS_DIR, engine)
    problems = validate(cases, votes, previous_claims=load_previous_claims(), complete=not args.partial)
    if problems:
        print("\n".join(problems))
        return EXIT_PROBLEMS
    print(f"OK: {len(cases)} Fälle, 0 Probleme")
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show is None and not args.cases.is_file():
        print(f"Keine Fälle: {args.cases} fehlt")
        return EXIT_PROBLEMS
    engine = make_engine()
    try:
        if args.show is not None:
            return show(args.show, engine)
        try:
            return check(args, engine)
        except ValueError as exc:  # a line of cases.jsonl the reader rejects
            print(exc)
            return EXIT_PROBLEMS
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(main())
