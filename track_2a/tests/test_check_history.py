from src.check_history import save_check, load_checks, matching_source


def test_history_roundtrip_source_identity_and_origins(tmp_path):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-1.7 original')
    database = tmp_path / 'private/checks.sqlite3'
    assert load_checks(database) == []
    fields = dict(result={'claim': 'Eine Behauptung', 'label': 0}, source_path=source,
                  date='2026-06-14', language='de', vote=None)
    first = save_check(database, **fields)
    second = save_check(database, **fields, notice='Saved demo')
    records = load_checks(database)
    assert [item['id'] for item in records] == [second['id'], first['id']]
    assert records[0]['origin'] == 'saved_demo' and records[1]['origin'] == 'live'
    assert records[1]['result'] == fields['result']
    assert database.stat().st_mode & 0o077 == 0
    assert matching_source(tmp_path, first) == source
    source.write_bytes(b'%PDF-1.7 changed')
    assert matching_source(tmp_path, first) is None
    assert matching_source(tmp_path, {**first, 'source_file': '../source.pdf'}) is None
    source.unlink()
    assert load_checks(database)[1]['result']['claim'] == 'Eine Behauptung'
    assert matching_source(tmp_path, first) is None


def test_trash_migrates_old_database_and_restores_without_losing_records(tmp_path):
    from src.check_history import set_removed
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-1.7')
    database = tmp_path / 'checks.sqlite3'
    fields = dict(result={'claim': 'Saved'}, source_path=source, date='2026-06-14', language='de', vote=None)
    first = save_check(database, **fields)
    assert load_checks(database, removed=True) == []
    assert set_removed(database, first['id'])
    assert load_checks(database) == []
    assert load_checks(database, removed=True) == [first]
    second = save_check(database, **fields)
    assert [x['id'] for x in load_checks(database)] == [second['id']]
    assert set_removed(database, first['id'], removed=False)
    assert len(load_checks(database)) == 2
    assert not load_checks(database, removed=True)
    assert not set_removed(database, 'missing')
    assert source.exists()


def test_exports_preserve_quotes_and_omit_internal_paths(tmp_path):
    import json, csv, io
    from src.check_history import export_checks
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'%PDF-1.7')
    result = {'claim': '=HYPERLINK("bad")', 'label': 0, 'label_name': 'Entailment',
              'evidence_sources': [{'page_number': 2, 'quote': 'Une citation.\nDeux lignes.'}],
              'booklet_path': '/private/user/source.pdf', 'retrieval_query_metadata': {'internal': '/private'}}
    record = save_check(tmp_path / 'checks.sqlite3', result=result, source_path=source,
                        date='2026-06-14', language='fr', vote='  +formula')
    exported = json.loads(export_checks([record]))
    assert exported['checks'][0]['result']['evidence_sources'] == result['evidence_sources']
    assert '/private' not in export_checks([record]).decode()
    assert exported['checks'][0]['result']['claim'] == result['claim']
    rows = list(csv.reader(io.StringIO(export_checks([record], 'csv').decode('utf-8-sig'))))
    assert rows[1][1].startswith("'=") and rows[1][5].startswith("'  +")


def test_history_filters_are_combined_inclusive_utc_and_read_only():
    from datetime import date
    from copy import deepcopy
    from src.check_history import filter_checks, export_checks
    import json
    records = [
        {'id': 'a', 'created_at': '2026-10-31T00:30:00+02:00', 'vote': 'Biodiversité',
         'result': {'claim': 'Le texte protège la nature.', 'label': 0, 'evidence_sources': [{'quote': 'Une forêt protégée.'}]}},
        {'id': 'b', 'created_at': '2026-10-31T23:59:59+00:00', 'vote': 'Mietrecht',
         'result': {'claim': 'Die Miete steigt.', 'label': 2}},
    ]
    before = deepcopy(records)
    assert [r['id'] for r in filter_checks(records, query='BIODIVERSITE FORET', label=0)] == ['a']
    day = date(2026, 10, 31)
    found = filter_checks(records, start=day, end=day)
    assert [r['id'] for r in found] == ['b']
    assert filter_checks(records, query='nature', label=2) == []
    assert filter_checks(records, start=date(2026, 11, 1), end=day) == []
    assert len(json.loads(export_checks(found))['checks']) == 1
    assert records == before


def test_origin_filter_and_sort_preserve_records_and_respect_offsets():
    from src.check_history import filter_checks, sort_checks
    rows = [
        {'id': 'a', 'origin': 'saved_demo', 'created_at': '2026-10-31T01:00:00+02:00', 'result': {'claim': 'Zulu'}},
        {'id': 'b', 'origin': 'live', 'created_at': '2026-10-31T00:00:00+00:00', 'result': {'claim': 'Alpha'}},
    ]
    assert [r['id'] for r in filter_checks(rows, origin='live')] == ['b']
    assert [r['id'] for r in sort_checks(rows)] == ['b', 'a']
    assert [r['id'] for r in sort_checks(rows, 'oldest')] == ['a', 'b']
    assert [r['id'] for r in sort_checks(rows, 'claim')] == ['b', 'a']
    assert [r['id'] for r in rows] == ['a', 'b']
