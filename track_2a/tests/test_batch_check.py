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
