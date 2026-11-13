"""Calibrate the guard thresholds on the dev split of the locked testset.

Only the local index and the E5 encoder are used: no Apertus, no LLM, no network. The cases are read through
`load_testset("dev")` and nowhere else; the test split stays closed.

For every dev case with a gold vote (or of kind mehrdeutig / ohne_abstimmung) the ranking of `search_votes` for the
claim's own wording is recorded once. Every grid point is then evaluated on these rows with `guard.assess`.
`none_below` is not calibrated: dev has a single case without a vote.

Usage (from track_2a/):
  HF_HOME=<model cache> HF_HUB_OFFLINE=1 ../.venv312/bin/python scripts/calibrate_guard.py
"""
import argparse
import contextlib
from datetime import date
import itertools
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Callable, Iterator, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config
from src.agent import guard, settings
from src.agent.guard import Thresholds
from src.agent.source_index import SourceIndex, VoteCandidate
from src.agent.testset import MANIFEST_PATH, EvalCase, load_testset
from src.agent.tools import MAX_QUERY_CHARS, VOTE_CANDIDATES
from src.booklet_catalog import load_catalog

GRID_MARGINS = (0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05)
GRID_HITS_RATIOS = (0.0, 0.25, 0.5, 0.75)
REPORT_PATH = Path(__file__).resolve().parents[1] / "results" / "genai" / "agent_guard_calibration.json"

RULE = ("Gewählt wird der Gitterpunkt mit dem kleinsten Tupel (verpasste Mehrdeutigkeiten, falsche Sicherheit, "
        "unnötige Rückfragen, Score-Abstand, Mindest-Trefferverhältnis). Eine verpasste Mehrdeutigkeit ist der "
        "schlimmste Fehler (der Agent rät eine Vorlage), dann folgt falsche Sicherheit (eindeutig, aber falsche "
        "Vorlage), dann die unnötige Rückfrage; bei Gleichstand gelten die kleinsten Werte.")
NONE_BELOW_NOTE = ("none_below wird nicht kalibriert und bleibt None: Dev hat genau einen Fall ohne Abstimmung "
                   "(ohne_abstimmung); aus einem einzelnen Fall lässt sich keine Schwelle ableiten. Sein Top-Score "
                   "steht nur zur Information in ohne_abstimmung_top_scores.")


def calibration_rows(cases: List[EvalCase], search: Callable[[str], List[VoteCandidate]]) -> List[dict]:
    """One row per case with a gold vote or of kind mehrdeutig / ohne_abstimmung; `search` ranks votes for a claim."""
    rows = []
    for case in cases:
        if case.vote_id is None and case.kind not in ("mehrdeutig", "ohne_abstimmung"):
            continue
        rows.append({"id": case.id, "kind": case.kind, "gold": case.vote_id,
                     "ambiguous_with": list(case.ambiguous_with),
                     "candidates": [{"vote_id": c.vote.vote_id, "score": c.score, "hits": c.hits}
                                    for c in search(case.claim)]})
    return rows


def _assess(row: dict, thresholds: Thresholds) -> guard.Assessment:
    """The guard on a stored row; it reads only the vote id, the score and the hits of a candidate."""
    candidates = [SimpleNamespace(vote=SimpleNamespace(vote_id=c["vote_id"]), score=c["score"], hits=c["hits"])
                  for c in row["candidates"]]
    return guard.assess(candidates, thresholds)


def evaluate(rows: List[dict], thresholds: Thresholds) -> dict:
    missed = false_confidence = unnecessary = 0
    for row in rows:
        gold = row["gold"]
        if row["kind"] == "mehrdeutig":
            assessment = _assess(row, thresholds)
            wanted = ({gold} if gold else set()) | set(row["ambiguous_with"])
            if assessment.kind != "mehrdeutig" or not wanted <= set(assessment.close):
                missed += 1
        elif gold is not None:
            assessment = _assess(row, thresholds)
            if assessment.kind == "eindeutig" and assessment.top != gold:
                false_confidence += 1
            elif assessment.kind == "mehrdeutig" and assessment.top == gold:
                unnecessary += 1
    return {"score_margin": thresholds.score_margin, "min_hits_ratio": thresholds.min_hits_ratio,
            "none_below": thresholds.none_below, "missed_ambiguous": missed, "false_confidence": false_confidence,
            "unnecessary_questions": unnecessary}


def grid(rows: List[dict]) -> List[dict]:
    return [evaluate(rows, Thresholds(margin, ratio, None))
            for margin, ratio in itertools.product(GRID_MARGINS, GRID_HITS_RATIOS)]


def _key(point: dict) -> tuple:
    return (point["missed_ambiguous"], point["false_confidence"], point["unnecessary_questions"],
            point["score_margin"], point["min_hits_ratio"])


def choose(rows: List[dict]) -> dict:
    return min(grid(rows), key=_key)


@contextlib.contextmanager
def open_search() -> Iterator[Callable[[str], List[VoteCandidate]]]:
    """The local index with the real encoder, searched as the agent's tools do (claim cut to MAX_QUERY_CHARS)."""
    index = SourceIndex(load_catalog(config.DATA_DIR / "booklet_catalog.json"), config.BOOKLETS_DIR,
                        index_dir=settings.INDEX_DIR)
    try:
        yield lambda claim: index.search_votes(claim[:MAX_QUERY_CHARS].strip(), k=VOTE_CANDIDATES)
    finally:
        index.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=REPORT_PATH, help="Pfad des Reports.")
    args = parser.parse_args(argv)
    cases = load_testset("dev")  # the only way to the cases; never the test split
    with open_search() as search:
        rows = calibration_rows(cases, search)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    provisional = Thresholds(0.02, 0.5, None)  # the values before this calibration
    report = {
        "date": date.today().isoformat(),
        "testset_cases_sha256": manifest["cases_sha256"],
        "split": "dev",
        "rows": rows,
        "grid": grid(rows),
        "chosen": choose(rows),
        "rule": RULE,
        "provisional": evaluate(rows, provisional),
        "none_below_note": NONE_BELOW_NOTE,
        "ohne_abstimmung_top_scores": [
            {"id": r["id"], "top_vote": r["candidates"][0]["vote_id"] if r["candidates"] else None,
             "top_score": r["candidates"][0]["score"] if r["candidates"] else None}
            for r in rows if r["kind"] == "ohne_abstimmung"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"chosen": report["chosen"], "provisional": report["provisional"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
