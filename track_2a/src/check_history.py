"""Local SQLite snapshots; reopening never performs inference."""
from datetime import datetime, timezone
import hashlib
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


def save_check(path, *, result, source_path, date, language, vote, notice=None):
    path, source_path = Path(path), Path(source_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with private permissions before SQLite opens it.
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    record = {'id': uuid4().hex, 'created_at': datetime.now(timezone.utc).isoformat(),
              'source_file': source_path.name, 'source_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
              'date': date, 'language': language, 'vote': vote,
              'origin': 'saved_demo' if notice else 'live', 'notice': notice,
              'result': result}
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS checks (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT NOT NULL)')
        connection.execute('INSERT INTO checks (id, created_at, payload) VALUES (?, ?, ?)',
                           (record['id'], record['created_at'], json.dumps(record, ensure_ascii=False)))
    return record


def load_checks(path, *, removed=False):
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(checks)')}
        if 'removed' not in columns:
            if removed:
                return []
            query, args = 'SELECT payload FROM checks ORDER BY created_at DESC', ()
        else:
            query, args = 'SELECT payload FROM checks WHERE removed = ? ORDER BY created_at DESC', (int(removed),)
        return [json.loads(row[0]) for row in connection.execute(query, args)]


def matching_source(directory, record):
    """Only reopen exactly the saved file contents, inside the booklet directory."""
    name = record['source_file']
    if Path(name).name != name:
        return None
    path = Path(directory) / name
    try:
        return path if hashlib.sha256(path.read_bytes()).hexdigest() == record['source_sha256'] else None
    except OSError:
        return None


def set_removed(path, record_id, removed=True):
    """Move to/from trash without deleting the snapshot. Migrate older databases in place."""
    if not Path(path).exists():
        return False
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute('PRAGMA table_info(checks)')}
        if 'removed' not in columns:
            connection.execute('ALTER TABLE checks ADD COLUMN removed INTEGER NOT NULL DEFAULT 0')
        changed = connection.execute('UPDATE checks SET removed = ? WHERE id = ?', (int(removed), record_id))
        return changed.rowcount == 1


def _export_record(record):
    # Export result information, never internal absolute paths or retrieval/debug metadata.
    fields = ('claim', 'label', 'label_name', 'reasoning', 'evidence', 'evidence_sources',
              'p_entail', 'p_neutral', 'p_contra', 'strategy', 'tokens_prompt',
              'tokens_completion', 'tokens_total', 'latency_ms', 'context_pages')
    return {**{key: record.get(key) for key in ('id', 'created_at', 'source_file', 'source_sha256',
                                              'date', 'language', 'vote', 'origin', 'notice')},
            'result': {key: record['result'][key] for key in fields if key in record['result']}}


def export_checks(records, format='json'):
    records = [_export_record(record) for record in records]
    if format == 'json':
        return (json.dumps({'schema_version': 1, 'kind': 'local_check_history_export',
                            'checks': records}, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if format != 'csv':
        raise ValueError('Unsupported export format')
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Zeitpunkt UTC', 'Behauptung', 'Ergebnis', 'Quellendatum', 'Sprache', 'Vorlage', 'Herkunft', 'Strategie'])
    for record in records:
        cells = [record['created_at'], record['result'].get('claim'), record['result'].get('label_name'),
                 record['date'], record['language'], record['vote'], record['origin'], record['result'].get('strategy')]
        safe = []
        for cell in cells:
            text = '' if cell is None else str(cell)
            if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
                text = "'" + text
            safe.append(text)
        writer.writerow(safe)
    return output.getvalue().encode('utf-8-sig')
