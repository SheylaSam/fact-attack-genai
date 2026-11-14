"""Build the agent's German source index over every vote of the catalog's booklets and report on it.

Real multilingual E5, no Apertus call, no network. Writes results/genai/agent_index_build.json with the number of
votes, the skipped PDFs, the build time, the index metadata and three control queries (top 3 votes each).
The control queries are a smoke check of the index, not a quality evaluation: nothing is tuned to pass them.

Usage (repo root):
  HF_HOME=<model-cache> HF_HUB_OFFLINE=1 python track_2a/scripts/build_agent_index.py
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.agent import settings
from src.agent.source_index import SourceIndex
from src.booklet_catalog import load_catalog
from src.vector_rag import E5Encoder

TOP = 3
# (query, [(source_id, fragment of the vote title)]): every expected vote should be among the top 3
CONTROL_QUERIES = [
    ("Die Mietrechtsvorlage stärkt die Vermieter.",
     [("2024-11-24_de", "Mietrecht: Kündigung wegen Eigenbedarfs"), ("2024-11-24_de", "Mietrecht: Untermiete")]),
    ("Das E-ID-Gesetz schützt die Daten.", [("2021-03-07_de", "E-ID-Gesetz"), ("2025-09-28_de", "E-ID-Gesetz")]),
    ("Der Zivildienst wird schwieriger.", [("2026-06-14_de", "Zivildienstgesetz")]),
]


def display(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def control(index, query, expected):
    top = index.search_votes(query, k=TOP)
    found = [{"vote_id": c.vote.vote_id, "title": c.vote.title, "score": round(c.score, 4), "hits": c.hits,
              "best_page": c.best_page, "snippet": c.snippet} for c in top]
    missing = [{"source_id": source_id, "title_contains": fragment} for source_id, fragment in expected
               if not any(c.vote.source_id == source_id and fragment.casefold() in c.vote.title.casefold()
                          for c in top)]
    return {"query": query, "expected": [{"source_id": s, "title_contains": f} for s, f in expected],
            "top": found, "expectation_met": not missing, "missing": missing}


def build_report(args):
    sources = load_catalog(args.catalog)
    started = time.perf_counter()
    encoder = E5Encoder()
    model_seconds = time.perf_counter() - started
    started = time.perf_counter()
    index = SourceIndex(sources, args.booklets, encoder=encoder, index_dir=args.index_dir,
                        languages=settings.LANGUAGES)
    build_seconds = time.perf_counter() - started
    try:
        by_source = {}
        for ref in index.votes.values():
            by_source[ref.source_id] = by_source.get(ref.source_id, 0) + 1
        catalog_titles = {Path(name).stem: set(entry.get("votes", [])) for name, entry in sources.items()}
        return {
            "kind": "agent_source_index_build_smoke_not_quality_evaluation",
            "catalog": display(args.catalog), "booklets": display(args.booklets), "index_dir": display(args.index_dir),
            "languages": list(settings.LANGUAGES),
            "votes": len(index.votes), "sources_indexed": len(by_source), "skipped": index.skipped,
            "votes_per_source": by_source,
            "whole_booklet_sources": sorted(ref.source_id for ref in index.votes.values()
                                            if ref.vote_id.endswith("#0")),
            "votes_without_catalog_title": sorted(ref.vote_id for ref in index.votes.values()
                                                  if ref.title not in catalog_titles[ref.source_id]),
            "votes_without_pages": sorted(ref.vote_id for ref in index.votes.values() if not ref.pages),
            "model_load_seconds": round(model_seconds, 1), "build_seconds": round(build_seconds, 1),
            "metadata": index.metadata,
            "control_queries": [control(index, query, expected) for query, expected in CONTROL_QUERIES],
        }
    finally:
        index.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, default=config.DATA_DIR / "booklet_catalog.json")
    parser.add_argument("--booklets", type=Path, default=config.BOOKLETS_DIR)
    parser.add_argument("--index-dir", type=Path, default=settings.INDEX_DIR)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "genai" / "agent_index_build.json")
    args = parser.parse_args()
    report = build_report(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['votes']} votes from {report['sources_indexed']} booklets, skipped {report['skipped']}, "
          f"model {report['model_load_seconds']} s, build {report['build_seconds']} s "
          f"(index reused: {report['metadata']['index_reused']})")
    for check in report["control_queries"]:
        print(f"\n{check['query']}  [expectation met: {check['expectation_met']}]")
        for c in check["top"]:
            print(f"  {c['score']:.4f}  hits={c['hits']:<2} {c['vote_id']:<18} p.{c['best_page']:<3} {c['title'][:80]}")
    print(f"\nReport written to {display(args.output)}")


if __name__ == "__main__":
    main()
