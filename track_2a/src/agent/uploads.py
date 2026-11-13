"""Own PDF uploads as sources of the agent: the one vote that stands for a whole uploaded booklet.

An upload is stored by `store_uploaded_source` as `custom_<sha256>.pdf` in the booklets directory, so the backend finds
it like a catalog booklet (`booklets_dir / f"{source_id}.pdf"`). The catalog's index knows nothing about it: the vote
is registered in the session's backend (`LocalBackend.add_vote`) and is always the check's fixed vote.
"""
import re
from pathlib import Path

from src.agent.source_index import VoteRef

UPLOAD_PREFIX = "custom_"
UPLOAD_TITLE = "Eigenes PDF"
_UPLOAD_SOURCE = re.compile(rf"{UPLOAD_PREFIX}[A-Za-z0-9]+")  # no dot, slash or other path part


def is_upload_source(source_id: str) -> bool:
    """True for the source id of an upload (`custom_<hash>`), never for a catalog id or anything path-like."""
    return isinstance(source_id, str) and _UPLOAD_SOURCE.fullmatch(source_id) is not None


def upload_vote(path: Path, language: str) -> VoteRef:
    """The vote of the stored upload at `path`: the whole booklet ("#0"), all pages (1-based), no official date.

    ValueError with a German message for a file that is missing, empty, no PDF, protected or unreadable; the message
    names neither the path nor the PDF library's own error."""
    path = Path(path)
    if path.suffix != ".pdf" or not is_upload_source(path.stem):
        raise ValueError("Das eigene PDF hat keinen gültigen Dateinamen.")
    try:
        with path.open("rb") as stream:
            head = stream.read(5)
    except OSError:
        raise ValueError("Das eigene PDF kann nicht gelesen werden.") from None
    if not head:
        raise ValueError("Das eigene PDF ist leer.")
    if head != b"%PDF-":
        raise ValueError("Die Datei ist kein PDF.")
    import fitz  # lazily, as in src/pdf_preview.py
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("Das eigene PDF ist passwortgeschützt und kann nicht gelesen werden.")
            pages = document.page_count
    except ValueError:
        raise
    except Exception:  # fitz raises its own FileDataError and RuntimeError subclasses for damaged files
        raise ValueError("Das eigene PDF ist beschädigt und kann nicht gelesen werden.") from None
    if pages < 1:
        raise ValueError("Das eigene PDF enthält keine Seiten.")
    return VoteRef(f"{path.stem}#0", path.stem, "", language.lower(), UPLOAD_TITLE, tuple(range(1, pages + 1)))
