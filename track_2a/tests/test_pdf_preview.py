import hashlib
from pathlib import Path
import fitz
import pytest
from src.pdf_preview import render_evidence_page


def _pdf(lines):
    with fitz.open() as document:
        page = document.new_page()
        for index, text in enumerate(lines):
            page.insert_text((60, 80 + index * 25), text)
        return document.tobytes()


def test_complete_quote_is_highlighted_without_modifying_source():
    data = _pdf(['The proposal changes the retirement age.'])
    before = hashlib.sha256(data).hexdigest()
    preview = render_evidence_page(data, 1, 'The proposal changes the retirement age.', 0)
    plain = render_evidence_page(data, 1, 'A quote that does not occur.', 0)
    assert preview.highlighted and preview.png.startswith(b'\x89PNG')
    assert preview.png != plain.png
    assert hashlib.sha256(data).hexdigest() == before
    with fitz.open(stream=data, filetype='pdf') as document:
        assert not list(document[0].annots() or [])


@pytest.mark.parametrize('quote,label', [
    ('The proposal changes a different policy.', 0),
    ('The proposal changes the retirement age.', 1),
    ('proposal', 0),
])
def test_missing_neutral_and_short_quotes_are_not_marked(quote, label):
    assert not render_evidence_page(_pdf(['The proposal changes the retirement age.']), 1, quote, label).highlighted


def test_repeated_quote_is_not_marked():
    text = 'The proposal changes the retirement age.'
    assert not render_evidence_page(_pdf([text, text]), 1, text, 2).highlighted


def test_invalid_page_is_explicit():
    with pytest.raises(ValueError):
        render_evidence_page(_pdf(['An original source sentence.']), 2, 'An original source sentence.', 0)


def test_real_italian_original_page(tmp_path):
    path = Path(__file__).parents[1] / 'data/booklets/2026-06-14_it.pdf'
    source = path.read_bytes()
    quote = 'L’Assemblea federale raccomanda al Popolo e ai Cantoni di respingere l’iniziativa.'
    preview = render_evidence_page(source, 22, quote, 0)
    assert preview.highlighted
    assert path.read_bytes() == source
