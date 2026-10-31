from copy import deepcopy
from html.parser import HTMLParser
import pytest
from src.check_report import render_check_report, batch_report_records


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize('language,title', [('de', 'Prüfbericht'), ('fr', 'Rapport de vérification'), ('it', 'Rapporto di verifica')])
def test_report_is_local_printable_and_escapes_user_content(language, title):
    record = {'origin': 'saved_demo', 'source_file': 'source.pdf', 'source_sha256': 'abc123',
              'source_url': 'javascript:alert(1)', 'created_at': '2026-10-31T12:00:00+00:00',
              'result': {'claim': '<script>alert(1)</script>', 'label': 0,
                         'booklet_path': '/private/user/path.pdf', 'evidence_sources': [
                             {'page_number': 2, 'quote': '<img src="https://tracker.example">Originale.'}]}}
    before = deepcopy(record)
    html = render_check_report([record], language, generated_at='2026-10-31T13:00:00+00:00').decode()
    tags = Tags()
    tags.feed(html)
    assert not any(tag in ('script', 'img', 'iframe', 'link') for tag, _ in tags.tags)
    assert title in html and f'lang="{language}"' in html
    assert '&lt;script&gt;' in html and '&lt;img' in html
    assert '@media print' in html and 'A4' in html
    assert 'abc123' in html and '2026-10-31T12:00:00+00:00' in html
    assert '/private/user' not in html and 'href="javascript:' not in html
    assert record == before


def test_report_keeps_failure_distinct_and_neutral_without_evidence():
    records = [{'error': 'offline', 'claim': 'Failed statement', 'result': None, 'attempts': 2},
               {'result': {'claim': 'Neutral statement', 'label': 1,
                           'evidence_sources': [{'quote': 'Do not show this as neutral evidence.'}]}}]
    html = render_check_report(records).decode()
    assert 'Technischer Fehler – keine Einordnung' in html
    assert 'Prüfversuche: 2' in html
    assert 'Do not show this' not in html
    assert 'Im Abstimmungsbüchlein nicht geklärt' in html
