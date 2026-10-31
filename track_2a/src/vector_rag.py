"""Optional dense retrieval experiment. No dependencies or model downloads on import."""
import argparse
import hashlib
import json
import math
from importlib.metadata import version
import os
from pathlib import Path
import time

from src.retriever import PassageRetriever


class E5Encoder:
    """Multilingual E5 with retrieval prefixes and tokenizer-based, verbatim chunks."""
    def __init__(self, model_name=None, revision=None, chunk_tokens=384, overlap=64):
        from sentence_transformers import SentenceTransformer
        self.model_name = model_name or os.getenv('RAG_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small')
        self.model = SentenceTransformer(self.model_name, revision=revision or os.getenv('RAG_MODEL_REVISION'),
                                         trust_remote_code=False, device=os.getenv('RAG_DEVICE', 'cpu'))
        resolved = getattr(self.model[0].auto_model.config, '_commit_hash', None)
        if not resolved:
            from huggingface_hub import try_to_load_from_cache
            cached = try_to_load_from_cache(self.model_name, 'config.json',
                                            revision=revision or os.getenv('RAG_MODEL_REVISION'))
            if isinstance(cached, str):
                resolved = Path(cached).parent.name
        if not resolved and Path(self.model_name).is_dir():
            digest = hashlib.sha256()
            for file in sorted(Path(self.model_name).rglob('*')):
                if file.is_file():
                    digest.update(str(file.relative_to(self.model_name)).encode())
                    with file.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
            resolved = digest.hexdigest()
        if not resolved:
            raise ValueError('Cannot determine model revision; use a pinned/cached model')
        self.identity = {'model': self.model_name, 'revision': resolved, 'sentence_transformers': version('sentence-transformers'),
                         'prefixes': ['query: ', 'passage: '], 'normalized': True,
                         'chunk_tokens': chunk_tokens, 'overlap': overlap}
        if not 0 <= overlap < chunk_tokens:
            raise ValueError('Require 0 <= overlap < chunk_tokens')
        # Leave room for special tokens and the E5 prefix; avoid silent truncation.
        if chunk_tokens + 16 > self.model.max_seq_length:
            raise ValueError('Chunk budget exceeds encoder context window')
        self.chunk_tokens, self.overlap = chunk_tokens, overlap

    def chunks(self, text):
        offsets = self.model.tokenizer(text, add_special_tokens=False, truncation=False,
                                       return_offsets_mapping=True, verbose=False)['offset_mapping']
        for start in range(0, len(offsets), self.chunk_tokens - self.overlap):
            stop = min(start + self.chunk_tokens, len(offsets))
            yield text[offsets[start][0]:offsets[stop - 1][1]]
            if stop == len(offsets):
                break

    def encode(self, texts, query=False):
        prefix = 'query: ' if query else 'passage: '
        if query:
            for text in texts:
                if len(self.model.tokenizer.encode(prefix + text)) > self.model.max_seq_length:
                    raise ValueError('Query exceeds embedding context window; shorten the claim/vote')
        return self.model.encode([prefix + text for text in texts], normalize_embeddings=True,
                                 show_progress_bar=False).tolist()


class VectorRetriever:
    """One isolated booklet index; persistent collections keyed by content and encoder settings.

    Dense scores use Qdrant cosine similarity. Optional BM25 fusion uses reciprocal ranks,
    not incomparable raw score scales. This experiment does not reuse part-selection cues.
    """
    def __init__(self, paragraphs, encoder=None, index_dir=None):
        from qdrant_client import QdrantClient, models
        self.encoder = encoder or E5Encoder()
        self.chunks = []
        for parent, paragraph in enumerate(paragraphs):
            for chunk_no, text in enumerate(self.encoder.chunks(paragraph['text'])):
                if text.strip():
                    self.chunks.append({**paragraph, 'text': text, 'parent_index': parent,
                                        'chunk_number': chunk_no})
        self.bm25 = PassageRetriever(self.chunks)
        signature = {'schema': 1, 'qdrant_client': version('qdrant-client'), 'encoder': self.encoder.identity, 'chunks': self.chunks}
        self.fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False,
                                                     default=str).encode()).hexdigest()
        self.metadata = {**self.encoder.identity, 'index_fingerprint': self.fingerprint,
                         'chunks': len(self.chunks), 'fusion': 'RRF(k=60)'}
        directory = index_dir or os.getenv('RAG_INDEX_DIR')
        if directory:
            location = Path(directory) / self.fingerprint
            location.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(location), force_disable_check_same_thread=True)
        else:
            self.client = QdrantClient(':memory:')
        self.collection = 'booklet'
        if self.client.collection_exists(self.collection):
            if self.client.count(self.collection, exact=True).count != len(self.chunks):
                raise ValueError('Incomplete index: remove its fingerprint directory and rebuild')
        elif self.chunks:
            vectors = self.encoder.encode([p['text'] for p in self.chunks])
            self._validate_vectors(vectors, len(self.chunks))
            self.client.create_collection(self.collection, vectors_config=models.VectorParams(
                size=len(vectors[0]), distance=models.Distance.COSINE))
            for start in range(0, len(vectors), 128):
                self.client.upsert(self.collection, points=[models.PointStruct(id=i, vector=vectors[i])
                                   for i in range(start, min(start + 128, len(vectors)))], wait=True)

    @staticmethod
    def _validate_vectors(vectors, expected):
        if len(vectors) != expected or not vectors or not vectors[0]:
            raise ValueError('Invalid embedding count or dimension')
        size = len(vectors[0])
        if any(len(v) != size or not all(math.isfinite(x) for x in v) or
               not any(x != 0 for x in v) for v in vectors):
            raise ValueError('Embeddings must be finite, nonzero and have equal dimensions')

    def retrieve(self, claim, top_k=8, target_vote=None, mode='vector', exclude_sections=None,
                 ensure_sections=None, section_caps=None):
        if mode not in ('vector', 'vector_hybrid'):
            raise ValueError('Expected vector or vector_hybrid')
        if top_k <= 0:
            raise ValueError('top_k must be positive')
        if not self.chunks:
            return []
        query = f'{target_vote}\n{claim}' if target_vote else claim
        vectors = self.encoder.encode([query], query=True)
        self._validate_vectors(vectors, 1)
        hits = self.client.query_points(self.collection, query=vectors[0], limit=len(self.chunks)).points
        dense_ids = [int(h.id) for h in hits]
        dense_scores = {int(h.id): float(h.score) for h in hits}
        rankings = [dense_ids]
        if mode == 'vector_hybrid':
            lexical = self.bm25.retrieve_hybrid(claim, top_k=len(self.chunks), target_vote=target_vote)
            identities = {(p['parent_index'], p['chunk_number']): i for i, p in enumerate(self.chunks)}
            rankings.append([identities[(p['parent_index'], p['chunk_number'])] for p in lexical])
        scores = dict(dense_scores)
        if mode == 'vector_hybrid':
            scores = {i: 0.0 for i in dense_ids}
            for ranking in rankings:
                for rank, i in enumerate(ranking):
                    scores[i] += 1 / (60 + rank + 1)
        ranked = sorted(dense_ids, key=lambda i: (-scores[i], i))
        ranked = [i for i in ranked if self.chunks[i].get('section') not in (exclude_sections or set())]
        # Keep the baseline's speaker guarantees and caps; never exceed top_k.
        counts, eligible = {}, []
        for i in ranked:
            section = self.chunks[i].get('section')
            if section_caps and section in section_caps and counts.get(section, 0) >= section_caps[section]:
                continue
            counts[section] = counts.get(section, 0) + 1
            eligible.append(i)
        selected = eligible[:top_k]
        protected = set()
        for section, n in (ensure_sections or {}).items():
            wanted = [i for i in eligible if self.chunks[i].get('section') == section][:n]
            protected.update(wanted)
            for i in wanted:
                if i in selected:
                    continue
                removable = [j for j in reversed(selected) if j not in protected]
                if removable:
                    selected[selected.index(removable[0])] = i
        selected.sort(key=lambda i: (-scores[i], i))
        return [{**self.chunks[i], 'retrieval_score': scores[i], 'dense_score': dense_scores[i],
                 'retrieval_backend': mode} for i in selected]

    def close(self):
        self.client.close()


def main():
    parser = argparse.ArgumentParser(description='Retrieval-only comparison; no Apertus calls')
    parser.add_argument('--booklet', type=Path, required=True)
    parser.add_argument('--claim', required=True)
    parser.add_argument('--vote')
    parser.add_argument('--top-k', type=int, default=8)
    parser.add_argument('--index-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from src.pdf_parser import PDFParser
    paragraphs = PDFParser().extract_paragraphs(args.booklet, passage_chars=None)
    start = time.perf_counter()
    vector = VectorRetriever(paragraphs, index_dir=args.index_dir)
    index_ms = (time.perf_counter() - start) * 1000
    try:
        variants = {}
        for mode in ('bm25', 'vector', 'vector_hybrid'):
            start = time.perf_counter()
            hits = (PassageRetriever(paragraphs).retrieve_hybrid(args.claim, top_k=args.top_k,
                    target_vote=args.vote) if mode == 'bm25' else vector.retrieve(
                    args.claim, top_k=args.top_k, target_vote=args.vote, mode=mode))
            variants[mode] = {'query_ms': (time.perf_counter() - start) * 1000, 'hits': hits}
        report = {'kind': 'retrieval_smoke_not_quality_evaluation', 'booklet': str(args.booklet),
                  'booklet_sha256': hashlib.sha256(args.booklet.read_bytes()).hexdigest(),
                  'claim': args.claim, 'vote': args.vote, 'top_k': args.top_k,
                  'embedding': vector.metadata, 'index_and_model_load_ms': index_ms,
                  'variants': variants, 'note': 'BM25 uses baseline paragraphs; vector variants use '
                  'token chunks. This smoke comparison does not isolate chunking effects.'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Retrieval comparison written to {args.output}')
    finally:
        vector.close()


if __name__ == '__main__':
    main()
