"""Official source metadata and locally available, date/language-specific PDFs."""
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

MONTHS = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli',
          'August', 'September', 'Oktober', 'November', 'Dezember')


def load_catalog(path):
    sources = json.loads(Path(path).read_text(encoding='utf-8'))['sources']
    for filename, entry in sources.items():
        if (not re.fullmatch(r'\d{4}-\d{2}-\d{2}_(de|fr|it)\.pdf', filename)
                or filename != f"{entry['date']}_{entry['language']}.pdf"
                or urlparse(entry['url']).scheme != 'https'
                or urlparse(entry['url']).hostname != 'www.bk.admin.ch'):
            raise ValueError('Invalid official booklet metadata')
    return sources


def available_sources(directory, sources):
    """Do not offer missing, empty or non-PDF files. Never include private uploads."""
    available = {}
    for name, entry in sources.items():
        try:
            with (Path(directory) / name).open('rb') as stream:
                valid = stream.read(5) == b'%PDF-'
        except OSError:
            valid = False
        if valid:
            available[name] = entry
    return available


def date_options(sources):
    result = {}
    for date in sorted({entry['date'] for entry in sources.values()}, reverse=True):
        year, month, day = map(int, date.split('-'))
        result[f'{day}. {MONTHS[month - 1]} {year}'] = date
    return result


def languages_for_date(sources, date):
    return [lang.upper() for lang in ('de', 'fr', 'it')
            if f'{date}_{lang}.pdf' in sources]


def _search_text(value):
    decomposed = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(char for char in decomposed if not unicodedata.combining(char))


def filter_sources(sources, query='', year='Alle Jahre'):
    """Match titles in all languages, then keep every available language of each date.

    This is a local metadata search, not PDF full-text search or semantic retrieval.
    """
    terms = _search_text(query).split()
    dates = set()
    for date in {entry['date'] for entry in sources.values()}:
        if year != 'Alle Jahre' and date[:4] != str(year):
            continue
        titles = [title for entry in sources.values() if entry['date'] == date
                  for title in entry.get('votes', [])]
        label = next(iter(date_options({'date': {'date': date}})))
        haystack = _search_text(' '.join([date, label, *titles]))
        if all(term in haystack for term in terms):
            dates.add(date)
    return {name: entry for name, entry in sources.items() if entry['date'] in dates}



def main():
    """Explicit download command; the UI never downloads documents implicitly."""
    from src import config
    from src.hf_dataset import _download
    sources = load_catalog(config.DATA_DIR / 'booklet_catalog.json')
    failed = []
    for name, entry in sources.items():
        if not _download(entry['url'], config.BOOKLETS_DIR / name):
            failed.append(name)
    available = available_sources(config.BOOKLETS_DIR, sources)
    print(f'{len(available)}/{len(sources)} official PDFs available')
    failed = sorted(set(failed) | (set(sources) - set(available)))
    if failed:
        raise SystemExit('Download failed: ' + ', '.join(failed))


if __name__ == '__main__':
    main()
