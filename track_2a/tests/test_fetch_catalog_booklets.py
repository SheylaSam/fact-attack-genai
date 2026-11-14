"""fetch_missing keeps valid booklets, downloads missing ones and never leaves an unverified file behind."""
import hashlib
import importlib.util
import io
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    'fetch_catalog_booklets', Path(__file__).parents[1] / 'scripts/fetch_catalog_booklets.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

PDF = b'%PDF-1.7\nbooklet body\n'
NAME = '2026-11-29_de.pdf'


def _sources(raw=PDF, sha=None):
    return {NAME: {'date': '2026-11-29', 'language': 'de', 'url': 'https://www.admin.ch/dam/de/x.pdf',
                   'sha256': sha or hashlib.sha256(raw).hexdigest(), 'votes': []}}


class _Opener:
    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        return io.BytesIO(self.data)


def test_fetch_missing_keeps_present_file(tmp_path):
    (tmp_path / NAME).write_bytes(PDF)
    opener = _Opener(b'unused')
    assert module.fetch_missing(_sources(), tmp_path, opener=opener) == {NAME: 'vorhanden'}
    assert opener.urls == []
    assert (tmp_path / NAME).read_bytes() == PDF


def test_fetch_missing_downloads_and_verifies(tmp_path):
    opener = _Opener(PDF)
    assert module.fetch_missing(_sources(), tmp_path, opener=opener) == {NAME: 'geladen'}
    assert opener.urls == ['https://www.admin.ch/dam/de/x.pdf']
    assert (tmp_path / NAME).read_bytes() == PDF
    assert not list(tmp_path.glob('*.part'))


def test_fetch_missing_replaces_invalid_file(tmp_path):
    (tmp_path / NAME).write_bytes(b'<html>error</html>')  # no %PDF- header
    assert module.fetch_missing(_sources(), tmp_path, opener=_Opener(PDF)) == {NAME: 'geladen'}
    (tmp_path / NAME).write_bytes(PDF + b'changed')  # PDF header but wrong hash
    assert module.fetch_missing(_sources(), tmp_path, opener=_Opener(PDF)) == {NAME: 'geladen'}
    assert (tmp_path / NAME).read_bytes() == PDF


def test_fetch_missing_rejects_hash_mismatch(tmp_path):
    with pytest.raises(ValueError, match=NAME):
        module.fetch_missing(_sources(sha='0' * 64), tmp_path, opener=_Opener(PDF))
    assert list(tmp_path.iterdir()) == []


def test_fetch_missing_rejects_non_pdf_download(tmp_path):
    html = b'<html>login</html>'
    with pytest.raises(ValueError, match=NAME):
        module.fetch_missing(_sources(html), tmp_path, opener=_Opener(html))
    assert list(tmp_path.iterdir()) == []


def test_fetch_missing_keeps_an_invalid_existing_file_when_the_download_fails(tmp_path):
    (tmp_path / NAME).write_bytes(PDF + b'changed')
    with pytest.raises(ValueError):
        module.fetch_missing(_sources(sha='0' * 64), tmp_path, opener=_Opener(PDF))
    assert [p.name for p in tmp_path.iterdir()] == [NAME]
    assert (tmp_path / NAME).read_bytes() == PDF + b'changed'


def test_fetch_missing_requires_a_catalog_hash(tmp_path):
    sources = _sources()
    del sources[NAME]['sha256']
    opener = _Opener(PDF)
    with pytest.raises(ValueError, match=NAME):
        module.fetch_missing(sources, tmp_path, opener=opener)
    assert opener.urls == [] and list(tmp_path.iterdir()) == []
