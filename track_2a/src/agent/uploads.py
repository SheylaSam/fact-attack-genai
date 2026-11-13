"""Session-local whole-booklet votes ("#0"): an own PDF upload, or a catalog booklet chosen without a vote.

An upload is stored by `store_uploaded_source` as `custom_<sha256>.pdf` in the booklets directory, so the backend finds
it like a catalog booklet (`booklets_dir / f"{source_id}.pdf"`). The catalog's index knows nothing about it: the vote
is registered in the session's backend (`LocalBackend.add_vote`) and is always the check's fixed vote.

A catalog booklet chosen in the app without a vote ("Automatisch erkennen") gets the same kind of vote when the index
has none for it (it has a "#0" only for booklets without a readable contents page): `whole_booklet_vote`, registered
the same way. So does a French or Italian booklet, which the German-only index does not know at all, with or without
one of its votes chosen. Its `engine_vote` is None, so the engine detects the vote inside the booklet as in the old
flow.
"""
import re
from pathlib import Path

from src.agent.source_index import WHOLE_BOOKLET_TITLE, VoteRef

UPLOAD_PREFIX = "custom_"
UPLOAD_TITLE = "Eigenes PDF"
_UPLOAD_SOURCE = re.compile(rf"{UPLOAD_PREFIX}[A-Za-z0-9]+")  # no dot, slash or other path part


def is_upload_source(source_id: str) -> bool:
    """True for the source id of an upload (`custom_<hash>`), never for a catalog id or anything path-like."""
    return isinstance(source_id, str) and _UPLOAD_SOURCE.fullmatch(source_id) is not None


def _all_pages(path: Path, name: str) -> tuple:
    """All page numbers (1-based) of the PDF at `path`. ValueError with a German message that starts with `name`
    for a file that is missing, empty, no PDF, protected or unreadable; it names neither the path nor the PDF
    library's own error."""
    try:
        with path.open("rb") as stream:
            head = stream.read(5)
    except OSError:
        raise ValueError(f"{name} kann nicht gelesen werden.") from None
    if not head:
        raise ValueError(f"{name} ist leer.")
    if head != b"%PDF-":
        raise ValueError("Die Datei ist kein PDF.")
    import fitz  # lazily, as in src/pdf_preview.py
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError(f"{name} ist passwortgeschützt und kann nicht gelesen werden.")
            pages = document.page_count
    except ValueError:
        raise
    except Exception:  # fitz raises its own FileDataError and RuntimeError subclasses for damaged files
        raise ValueError(f"{name} ist beschädigt und kann nicht gelesen werden.") from None
    if pages < 1:
        raise ValueError(f"{name} enthält keine Seiten.")
    return tuple(range(1, pages + 1))


def upload_vote(path: Path, language: str) -> VoteRef:
    """The vote of the stored upload at `path`: the whole booklet ("#0"), all pages (1-based), no official date.

    ValueError with a German message for a file that is missing, empty, no PDF, protected or unreadable; the message
    names neither the path nor the PDF library's own error."""
    path = Path(path)
    if path.suffix != ".pdf" or not is_upload_source(path.stem):
        raise ValueError("Das eigene PDF hat keinen gültigen Dateinamen.")
    pages = _all_pages(path, "Das eigene PDF")
    return VoteRef(f"{path.stem}#0", path.stem, "", language.lower(), UPLOAD_TITLE, pages)


def whole_booklet_vote(booklets_dir: Path, known: VoteRef) -> VoteRef:
    """The whole-booklet vote ("#0") of the catalog booklet that `known`, a vote of the index or a ref built from the
    booklet's catalog entry, belongs to: its date and language, the title `WHOLE_BOOKLET_TITLE` and all pages of
    `booklets_dir / f"{source_id}.pdf"`. The file name comes from the index or the catalog, never from free input.
    ValueError (German) if that PDF cannot be read."""
    pages = _all_pages(Path(booklets_dir) / f"{known.source_id}.pdf", "Das Büchlein")
    return VoteRef(f"{known.source_id}#0", known.source_id, known.date, known.language, WHOLE_BOOKLET_TITLE, pages)
