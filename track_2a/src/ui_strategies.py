"""Explicit interactive strategies; vector modes require a UI opt-in."""
from importlib.util import find_spec
from pathlib import Path
import hashlib
import os
import tempfile
import json

STANDARD='Standard (Hybrid)'
FULL='Ganzes Büchlein'
DENSE='Vektorsuche (Experiment)'
FUSED='BM25 + Vektor (Experiment)'
STRATEGIES={STANDARD:'hybrid',FULL:'full',DENSE:'vector',FUSED:'vector_hybrid'}


def strategy_options(experiments=False):
    return list(STRATEGIES) if experiments else [STANDARD,FULL]


def strategy_from_label(label):
    if label not in STRATEGIES:
        raise ValueError('Unbekannte Kontextstrategie')
    return STRATEGIES[label]


def rag_available():
    return all(find_spec(name) is not None for name in ('qdrant_client','sentence_transformers'))


def uploaded_source_path(directory, contents):
    """Immutable identity: replacing an upload with the same filename cannot reuse stale text."""
    return Path(directory)/f"custom_{hashlib.sha256(contents).hexdigest()}.pdf"


def store_uploaded_source(directory, contents):
    path = uploaded_source_path(directory, contents)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == path.stem.removeprefix('custom_'):
        return path
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.upload-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def load_completed_dev42(path):
    try:
        report = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    if (report.get('kind') != 'paired_real_nli_observed_dev' or report.get('complete') is not True
            or report.get('mock') is not False or report.get('paired_case_count') != 42):
        return None
    summaries = [r for r in report.get('summary', []) if r.get('group') == 'all']
    if len(summaries) != 3 or {r.get('variant') for r in summaries} != {'baseline_native','vector','vector_hybrid'}:
        return None
    return report
