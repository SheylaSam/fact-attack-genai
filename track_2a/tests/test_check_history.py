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
