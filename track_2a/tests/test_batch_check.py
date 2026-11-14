from types import SimpleNamespace
import pytest
from src.batch_check import suggest_statements, run_checks


def test_local_suggestions_preserve_numbers_and_deduplicate():
    assert suggest_statements('Der Satz nennt 3.5 Prozent. Un autre texte!\nUna frase.\nUna frase.') == ['Der Satz nennt 3.5 Prozent.', 'Un autre texte!', 'Una frase.']
    assert suggest_statements('  ') == []
    with pytest.raises(ValueError):
        suggest_statements('a' * 20001)


def test_checks_keep_order_partial_failure_and_save_failure_without_retry():
    calls, saved = [], []
    def verify(claim):
        calls.append(claim)
        if claim == 'failed':
            raise RuntimeError('offline')
        return SimpleNamespace(error=None, claim=claim)
    def save(result):
        saved.append(result.claim)
        if result.claim == 'savefailed':
            raise OSError('disk full')
    output = run_checks(['ok', 'failed', 'savefailed'], verify, save)
    assert calls == ['ok', 'failed', 'savefailed']
    assert saved == ['ok', 'savefailed']
    assert output[0]['result'].claim == 'ok'
    assert output[1]['result'] is None and output[1]['error'] == 'offline'
    assert output[2]['result'].claim == 'savefailed' and output[2]['save_error']
    for invalid in ([], ['x'] * 6):
        with pytest.raises(ValueError):
            run_checks(invalid, verify)


def test_retry_only_errors_preserves_success_and_counts_attempts():
    from src.batch_check import retry_failed, batch_summary
    good = {'claim': 'ok', 'result': SimpleNamespace(error=None, claim='ok'), 'error': None,
            'save_error': 'disk full', 'attempts': 1}
    failed = {'claim': 'failed', 'result': None, 'error': 'offline', 'save_error': None, 'attempts': 1}
    calls, saved, progress = [], [], []
    def verify(claim):
        calls.append(claim)
        return SimpleNamespace(error=None, claim=claim)
    retried = retry_failed([good, failed], verify, lambda result: saved.append(result.claim),
                           lambda done, total, claim: progress.append((done, total, claim)))
    assert calls == saved == ['failed']
    assert retried[0] is good and retried[1]['attempts'] == 2
    assert failed['error'] == 'offline' and failed['attempts'] == 1
    assert progress == [(0, 1, 'failed'), (1, 1, 'failed')]
    assert batch_summary(retried) == {'total': 2, 'successful': 2, 'failed': 0, 'save_failed': 1}
    assert retry_failed(retried, verify) == retried
    assert calls == ['failed']


def test_progress_finishes_even_when_every_check_fails():
    progress = []
    def fail(claim):
        raise RuntimeError('offline')
    output = run_checks(['a', 'b'], fail, on_progress=lambda done, total, claim: progress.append((done, total)))
    assert all(outcome['error'] for outcome in output)
    assert progress == [(0, 2), (1, 2), (1, 2), (2, 2)]


def test_batch_export_includes_failures_and_omits_internal_paths():
    import json, csv, io
    from src.batch_check import export_batch
    result = SimpleNamespace(model_dump=lambda **kwargs: {'label': 1, 'label_name': 'Neutral',
                             'booklet_path': '/private/file.pdf', 'evidence': [], 'retrieval_query_metadata': {'private': 1}})
    outcomes = [{'claim': 'Une affirmation.', 'result': result, 'error': None, 'save_error': None, 'attempts': 1},
                {'claim': '=FORMULA()', 'result': None, 'error': 'offline', 'save_error': None, 'attempts': 2}]
    context = {'source_file': 'source.pdf', 'date': '2026-06-14', 'language': 'fr', 'strategy': 'hybrid',
               'internal_path': '/private/file.pdf'}
    raw = export_batch(outcomes, context)
    exported = json.loads(raw)
    assert len(exported['outcomes']) == 2 and exported['summary']['failed'] == 1
    assert exported['outcomes'][1]['attempts'] == 2
    assert b'/private' not in raw and b'retrieval_query_metadata' not in raw
    csv_rows = list(csv.reader(io.StringIO(export_batch(outcomes, context, 'csv').decode('utf-8-sig'))))
    assert csv_rows[2][0] == "'=FORMULA()"
    assert csv_rows[2][1] == 'failed'


def test_exception_without_message_still_counts_as_retryable_failure():
    from src.batch_check import batch_summary
    def fail(claim):
        raise RuntimeError()
    outcomes = run_checks(['a'], fail)
    assert outcomes[0]['error'] == 'RuntimeError'
    assert batch_summary(outcomes)['failed'] == 1
