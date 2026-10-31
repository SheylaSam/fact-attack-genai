"""Render original PDF pages in memory; never alter the source file."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PagePreview:
    png: bytes
    highlighted: bool
    reason: str


def _normalise(text):
    text = text.replace('\u00ad', '')
    text = re.sub(r'-\s*\n\s*(?=\w)', '', text)
    return ' '.join(text.split()).casefold()


def render_evidence_page(pdf_bytes, page_number, quote, label, scale=1.5):
    """Highlight only a complete, unique quote on a 1-based physical PDF page.

    Missing/ambiguous text produces an unmarked original page and an explicit reason.
    Labels describe agreement with the source, not truth. Neutral has no highlights.
    """
    import fitz
    with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
        if not isinstance(page_number, int) or not 1 <= page_number <= len(document):
            raise ValueError('Die Belegseite liegt ausserhalb des PDFs.')
        page = document[page_number - 1]
        clean = ' '.join(quote.split())
        normal_quote = _normalise(quote)
        highlighted = False
        reason = 'Die Belegstelle konnte auf dieser PDF-Seite nicht eindeutig zugeordnet werden. Die Seite wird ohne Markierung angezeigt.'
        if label != 1 and len(normal_quote) >= 12:
            # Do not select a short prefix or mark every repetition of a phrase.
            occurrences = _normalise(page.get_text('text', sort=True)).count(normal_quote)
            if occurrences == 1:
                quads = page.search_for(clean, quads=True)
                if quads:
                    annotation = page.add_highlight_annot(quads)
                    annotation.set_colors(stroke=(0.35, 0.85, 0.65) if label == 0 else (1.0, 0.6, 0.35))
                    annotation.set_opacity(0.35)
                    annotation.update()
                    highlighted = True
                    reason = 'Markiert ist der angezeigte Belegauszug auf der Originalseite.'
        if label == 1:
            reason = 'Für ein nicht geklärtes Ergebnis wird keine Belegstelle markiert.'
        return PagePreview(page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png'),
                           highlighted, reason)
