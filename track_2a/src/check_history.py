"""Local SQLite snapshots; reopening never performs inference."""
from datetime import datetime, timezone
import hashlib
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
        connection.execute('INSERT INTO checks VALUES (?, ?, ?)',
                           (record['id'], record['created_at'], json.dumps(record, ensure_ascii=False)))
    return record


def load_checks(path):
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT payload FROM checks ORDER BY created_at DESC')]


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
