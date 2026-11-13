"""Test server for the MCP tests: the real `mcp_server.serve` over a `LocalBackend` with a fake index and engine.

Run as a script from any directory:  python tests/fixtures/mcp_fake_server.py [--laerm] [--leer]
Not collected as a test (the name has no `test_` prefix). Task 6 (the MCP client) uses the same switches.

* `--laerm`: the backend setup writes "Lärm" to stdout (a library that prints while the server starts).
* `--leer`: `search_votes` returns no candidates.
* An `anfrage` of "STIRB" (`suche_vorlagen`, `suche_passagen_vektor`) ends the process at once with `os._exit(1)`
  (a server that dies mid-check); so does a `behauptung` of "STIRB" to `suche_passagen`. Task 6 uses
  `suche_vorlagen` with it.
* Passages carry numpy scalars (`np.int64` page numbers, an `np.float32` score), as the engine's do.
"""
import argparse
import os
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.agent import mcp_server
from src.agent.search_backend import LocalBackend
from src.agent.source_index import VoteCandidate, VoteRef

V1 = VoteRef('2024-11-24_de#1', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Untermiete)', (2, 3))
V2 = VoteRef('2024-11-24_de#2', '2024-11-24_de', '2024-11-24', 'de',
             'Änderung des Obligationenrechts (Mietrecht: Kündigung wegen Eigenbedarfs)', (4, 5))
CANDIDATES = [
    VoteCandidate(vote=V1, score=0.873, hits=12, best_page=2, snippet='Anfang der besten Textstelle zu V1'),
    VoteCandidate(vote=V2, score=0.868, hits=7, best_page=4, snippet='Anfang der besten Textstelle zu V2'),
]


def _die_on_cue(text):
    if text == 'STIRB':
        os._exit(1)


class FakeIndex:
    def __init__(self, empty=False):
        self.votes = {vote.vote_id: vote for vote in (V1, V2)}
        self.empty = empty

    def search_votes(self, query, k=5):
        _die_on_cue(query)
        return [] if self.empty else list(CANDIDATES)

    def search_passages(self, vote_id, query, k=8):
        _die_on_cue(query)
        return [{'page_number': np.int64(self.votes[vote_id].pages[0]), 'text': f'Vektortreffer zu {query}',
                 'section': 'COM', 'vote_id': vote_id, 'retrieval_score': np.float32(0.9)}]


class FakeEngine:
    def retrieve_passages(self, claim, booklet_pdf, vote=None, claim_language=None, booklet_language=None, top_k=8):
        _die_on_cue(claim)
        return [{'page_number': np.int64(2), 'text': f'Treffer zu {claim}', 'section': 'COM'}]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--laerm', action='store_true')
    parser.add_argument('--leer', action='store_true')
    args = parser.parse_args()

    def make_fake_backend():
        if args.laerm:
            print('Lärm')
        return LocalBackend(FakeIndex(empty=args.leer), FakeEngine(), Path('/booklets'))

    mcp_server.serve(make_fake_backend)


if __name__ == '__main__':
    main()
