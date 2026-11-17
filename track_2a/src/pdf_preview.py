"""Render original PDF pages in memory; never alter the source file."""
from dataclasses import dataclass

from src.text_utils import dehyphenate


@dataclass(frozen=True)
class PagePreview:
    png: bytes
    highlighted: bool
    reason: str


def _normalise(text):
    return ' '.join(dehyphenate(text).split()).casefold()


def _word_text(page):
    """Page text as single-spaced words, plus the word boxes behind every character.

    A word broken at a line end (soft or hard hyphen) is joined with the first word of the next line
    the same way the booklet parser does it, so a quote checked against parsed text is found here too.
    """
    words = page.get_text('words')
    text, owners = '', []
    i = 0
    while i < len(words):
        piece, members = words[i][4], [i]
        while (members[-1] + 1 < len(words) and piece.endswith(('\u00ad', '-'))
               and words[members[-1]][5:7] != words[members[-1] + 1][5:7]):
            joined = dehyphenate(piece + '\n' + words[members[-1] + 1][4])
            if any(char.isspace() for char in joined):
                break
            piece = joined
            members.append(members[-1] + 1)
        piece = piece.replace('\u00ad', '').casefold()
        if text:
            text += ' '
            owners.append(())
        text += piece
        owners.extend([tuple(members)] * len(piece))
        i = members[-1] + 1
    return text, owners, words


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
        normal_quote = _normalise(quote)
        highlighted = False
        reason = 'Die Belegstelle konnte auf dieser PDF-Seite nicht eindeutig zugeordnet werden. Die Seite wird ohne Markierung angezeigt.'
        if label != 1 and len(normal_quote) >= 12:
            # Do not select a short prefix or mark every repetition of a phrase.
            text, owners, words = _word_text(page)
            if text.count(normal_quote) == 1:
                start = text.find(normal_quote)
                members = sorted({m for owner in owners[start:start + len(normal_quote)] for m in owner})
                lines = {}
                for m in members:  # one box per line, like a normal text selection
                    lines[words[m][5:7]] = lines.get(words[m][5:7], fitz.Rect(words[m][:4])) | fitz.Rect(words[m][:4])
                if lines:
                    annotation = page.add_highlight_annot(list(lines.values()))
                    annotation.set_colors(stroke=(0.35, 0.85, 0.65) if label == 0 else (1.0, 0.6, 0.35))
                    annotation.set_opacity(0.35)
                    annotation.update()
                    highlighted = True
                    reason = 'Markiert ist der angezeigte Belegauszug auf der Originalseite.'
        if label == 1:
            reason = 'Für ein nicht belegtes Ergebnis wird keine Belegstelle markiert.'
        return PagePreview(page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes('png'),
                           highlighted, reason)
