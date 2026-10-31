import json
import pytest
from src.booklet_catalog import load_catalog, available_sources, date_options, languages_for_date


def test_availability_is_specific_to_date_and_language(tmp_path):
    sources = {f'{date}_{lang}.pdf': {'date': date, 'language': lang}
               for date, lang in [('2026-06-14', 'de'), ('2026-06-14', 'fr'), ('2024-09-22', 'it')]}
    (tmp_path / '2026-06-14_de.pdf').write_bytes(b'%PDF-1.7\n')
    (tmp_path / '2026-06-14_fr.pdf').write_bytes(b'<html>error</html>')
    (tmp_path / 'custom_private.pdf').write_bytes(b'%PDF-1.7\n')
    found = available_sources(tmp_path, sources)
    assert list(found) == ['2026-06-14_de.pdf']
    assert languages_for_date(found, '2026-06-14') == ['DE']
    assert date_options(found) == {'14. Juni 2026': '2026-06-14'}
    (tmp_path / '2024-09-22_it.pdf').write_bytes(b'%PDF-1.7\n')
    assert list(date_options(available_sources(tmp_path, sources)).values()) == ['2026-06-14', '2024-09-22']


def test_manifest_rejects_non_official_urls_and_paths(tmp_path):
    entry = {'date': '2026-06-14', 'language': 'de', 'url': 'https://www.bk.admin.ch/dam/de/test.pdf', 'votes': []}
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'sources': {'2026-06-14_de.pdf': entry}}))
    assert load_catalog(path)['2026-06-14_de.pdf'] == entry
    for filename, url in [('2026-06-14_de.pdf', 'https://example.org/test.pdf'), ('../2026-06-14_de.pdf', entry['url'])]:
        path.write_text(json.dumps({'sources': {filename: {**entry, 'url': url}}}))
        with pytest.raises(ValueError):
            load_catalog(path)
