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


def test_title_search_matches_across_languages_and_keeps_date_languages():
    from src.booklet_catalog import filter_sources
    sources = {
        '2024-09-22_de.pdf': {'date': '2024-09-22', 'votes': ['Biodiversitätsinitiative']},
        '2024-09-22_fr.pdf': {'date': '2024-09-22', 'votes': ['Initiative biodiversité']},
        '2024-11-24_de.pdf': {'date': '2024-11-24', 'votes': ['Mietrecht']},
    }
    assert set(filter_sources(sources, 'BIODIVERSITE')) == {'2024-09-22_de.pdf', '2024-09-22_fr.pdf'}
    assert len(filter_sources(sources, 'biodiversite', '2024')) == 2
    assert not filter_sources(sources, 'biodiversite', '2025')
    assert not filter_sources(sources, 'Mietrecht Unbekannt')
    assert len(filter_sources(sources, 'September 2024')) == 2
    assert filter_sources(sources, '   ') == sources


def _write_catalog(tmp_path, url):
    entry = {'date': '2026-11-29', 'language': 'de', 'url': url, 'votes': []}
    path = tmp_path / 'catalog.json'
    path.write_text(json.dumps({'sources': {'2026-11-29_de.pdf': entry}}))
    return path


def test_load_catalog_accepts_www_admin_ch(tmp_path):
    from src.booklet_catalog import OFFICIAL_HOSTS
    assert OFFICIAL_HOSTS == ('www.bk.admin.ch', 'www.admin.ch')
    for host in OFFICIAL_HOSTS:
        path = _write_catalog(tmp_path, f'https://{host}/dam/de/x.pdf')
        assert '2026-11-29_de.pdf' in load_catalog(path)


def test_load_catalog_rejects_other_hosts(tmp_path):
    for url in ['https://evil.example/x.pdf', 'http://www.admin.ch/x.pdf',
                'https://www.admin.ch.evil.example/x.pdf', 'https://admin.ch/x.pdf']:
        with pytest.raises(ValueError):
            load_catalog(_write_catalog(tmp_path, url))


def test_catalog_lists_new_booklets():
    from src import config
    sources = load_catalog(config.DATA_DIR / 'booklet_catalog.json')
    for date in ('2026-09-27', '2026-11-29'):
        for lang in ('de', 'fr', 'it'):
            assert f'{date}_{lang}.pdf' in sources
    assert sources['2026-09-27_de.pdf']['sha256'] == '6d27a4c6c5ed0e8583a49864979dcc2ddeeb4c65e895b97a2df3fa1d089dbc15'
    assert sources['2026-11-29_de.pdf']['sha256'] == '391b1f2941820c9859b0fef5ac6dd366e325076ebd125f0c5ecd763fea428be2'
    assert len(sources['2026-11-29_de.pdf']['votes']) == 4 and len(sources['2026-09-27_de.pdf']['votes']) == 2
    for name in sources:
        if name.startswith(('2026-09-27', '2026-11-29')):
            assert len(sources[name]['sha256']) == 64
            assert len(sources[name]['votes']) == (2 if name.startswith('2026-09-27') else 4)
