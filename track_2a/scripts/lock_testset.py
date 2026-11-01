"""Lock the agent testset: check it, hash it and write `data/genai/testset/manifest.json`.

Usage (from track_2a/):
  python scripts/lock_testset.py [--review PATH]
  python scripts/lock_testset.py --status [--review PATH]

`--status` only reads: it prints where the review stands (missing, stale and disagreeing judgements, the blind
agreement) and exits 0 if the set could be locked as far as the review goes. It copies nothing, writes no manifest and
starts no engine, and it works on a locked set too. Run it on an export before locking.

Locking (without `--status`):
`--review` is the export of the review page (`build_label_review.py`): it is copied to
`data/genai/testset/label_review.jsonl` first. Without it the review stored there is used. Locking needs the finished
set (`check_testset.py` without `--partial`: counts, quotes, no repeated claims) and a judgement of Sheyla for every
case that is current (not given for an older version of the case) and agrees with the draft. The script records
`git rev-parse HEAD` and whether the working tree is dirty; changes under `data/genai/testset/` do not count (the review
was just copied there and the lock commit follows). A locked set is never changed: that needs a new testset. Afterwards
only `load_testset("dev")` hands out cases; the test part stays closed until stage 5. The engine uses the mock Apertus
client: no request leaves the machine.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_testset import load_previous_claims, make_engine
from src import config
from src.agent.testset import (ALREADY_LOCKED, CASES_PATH, MANIFEST_PATH, REVIEW_PATH, TESTSET_DIR, LockRefused,
                               ReviewStatus, load_vote_catalog, lock, read_cases, read_review, review_status)
from src.booklet_catalog import load_catalog

EXIT_OK, EXIT_PROBLEMS = 0, 1
TESTSET_PREFIX = TESTSET_DIR.relative_to(config.REPO_ROOT).as_posix() + "/"  # git's paths are relative to the repo root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--review", type=Path, metavar="PATH",
                        help=f"Exportdatei der Prüfseite, wird nach {REVIEW_PATH} kopiert "
                             "(Standard: die dort liegende)")
    parser.add_argument("--status", action="store_true",
                        help="nur lesen: Stand der Labelprüfung ausgeben (fehlend, veraltet, abweichend), "
                             "nichts kopieren, nicht sperren")
    return parser


def changed_paths(status: str) -> List[str]:
    """The paths in the output of `git status --porcelain -z` (a rename or copy lists both of its paths)."""
    paths: List[str] = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            paths.append(next(entries, ""))  # the original path follows as its own entry
    return [path for path in paths if path]


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"git {' '.join(args)} ist fehlgeschlagen: {detail.strip()}") from None
    return result.stdout


def git_state(repo: Path = config.REPO_ROOT, ignore: str = TESTSET_PREFIX) -> Tuple[str, bool]:
    """`git rev-parse HEAD` and whether the tree has changes (untracked files count) outside the `ignore` directory."""
    commit = _git(repo, "rev-parse", "HEAD").strip()
    status = _git(repo, "status", "--porcelain", "-z")
    return commit, any(not path.startswith(ignore) for path in changed_paths(status))


def _same_file(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve()


def summary(manifest: dict) -> str:
    counts = manifest["counts"]["split_group"]
    review = manifest["review"]
    changed = review["changed_after_first_review"]
    tree = "nicht sauber (ausser data/genai/testset/)" if manifest["git_dirty"] else "sauber"
    lines = [f"Testset gesperrt: {MANIFEST_PATH}",
             f"{review['cases']} Fälle ({', '.join(f'{name} {count}' for name, count in counts.items())})",
             f"Erstes Urteil stimmte überein: {review['first_judgement_agrees']} von {review['cases']}",
             f"Nach dem ersten Urteil geändert: {len(changed)}" + (f" ({', '.join(changed)})" if changed else ""),
             f"Commit: {manifest['git_commit']}, Arbeitsbaum {tree}",
             f"cases.jsonl SHA-256: {manifest['cases_sha256']}",
             f"Büchlein: {len(manifest['booklets'])}",
             f"Nächster Schritt: {TESTSET_PREFIX} committen (Fälle, Review, Manifest); danach bleibt alles so."]
    return "\n".join(lines)


def _ids(names: Sequence[str]) -> str:
    return f" ({len(names)}): {', '.join(names)}" if names else ": keine"


def status_report(total: int, status: ReviewStatus, review_path: Path, locked: bool) -> str:
    """The review status as German text; `total` is the number of cases."""
    open_cases = len(status.missing) + len(status.stale) + len(status.disagree)
    changed = status.changed_after_first_review
    lines = [f"Stand der Labelprüfung (nur gelesen, nichts geschrieben): {review_path}"]
    if locked:
        lines.append(f"Hinweis: {ALREADY_LOCKED}")
    lines += [f"{total} Fälle",
              f"Beurteilt, stimmt überein: {total - open_cases}",
              f"Fehlend{_ids(status.missing)}",
              f"Veraltet{_ids(status.stale)}",
              f"Abweichend{_ids(status.disagree)}",
              f"Erstes Urteil stimmte überein: {status.first_agree} von {total}",
              f"Nach dem ersten Urteil geändert{_ids(changed)}",
              "Bereit zum Sperren (soweit die Review reicht): `lock_testset.py --review <Export>`" if not open_cases
              else "Noch nicht bereit zum Sperren: fehlende und veraltete Fälle beurteilen lassen, "
                   "abweichende einzeln klären"]
    return "\n".join(lines)


def show_status(review_path: Path) -> int:
    """`--status`: read the cases and the review, print where it stands. Writes and copies nothing."""
    if not CASES_PATH.is_file():
        print(f"Keine Fälle: {CASES_PATH} fehlt")
        return EXIT_PROBLEMS
    if not review_path.is_file():
        print(f"Keine Review: {review_path} fehlt")
        return EXIT_PROBLEMS
    try:
        cases = read_cases(CASES_PATH)
        status = review_status(cases, read_review(review_path))
    except ValueError as exc:  # a bad line of cases.jsonl or of the export
        print(exc)
        return EXIT_PROBLEMS
    print(status_report(len(cases), status, review_path, MANIFEST_PATH.exists()))
    return EXIT_PROBLEMS if status.missing or status.stale or status.disagree else EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        return show_status(args.review or REVIEW_PATH)
    if MANIFEST_PATH.exists():  # before the review is copied: a locked review is never overwritten
        print(ALREADY_LOCKED)
        return EXIT_PROBLEMS
    if not CASES_PATH.is_file():
        print(f"Keine Fälle: {CASES_PATH} fehlt")
        return EXIT_PROBLEMS
    source = args.review or REVIEW_PATH
    if not source.is_file():
        print(f"Keine Review: {source} fehlt")
        return EXIT_PROBLEMS
    try:
        commit, dirty = git_state()
    except RuntimeError as exc:
        print(exc)
        return EXIT_PROBLEMS
    if not _same_file(source, REVIEW_PATH):
        REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, REVIEW_PATH)
    engine = make_engine()
    try:
        sources = load_catalog(config.DATA_DIR / "booklet_catalog.json")
        votes = load_vote_catalog(sources, config.BOOKLETS_DIR, engine)
        manifest = lock(votes=votes, sources=sources, cases_path=CASES_PATH, review_path=REVIEW_PATH,
                        manifest_path=MANIFEST_PATH, git_commit=commit, git_dirty=dirty,
                        now=datetime.now(timezone.utc), previous_claims=load_previous_claims())
    except LockRefused as exc:
        print("Sperren abgelehnt:")
        print("\n".join(f"- {problem}" for problem in exc.problems))
        return EXIT_PROBLEMS
    finally:
        engine.close()
    print(summary(manifest))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
