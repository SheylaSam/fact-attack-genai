import importlib.util
import io
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('fixed_context_nli_smoke',Path(__file__).parents[1]/'scripts/fixed_context_nli_smoke.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_budget_includes_reconnect_and_never_sends_after_cap(tmp_path):
    calls=[]
    class Connection:
        def request(self,*args,**kwargs):
            calls.append(args)
            if len(calls)==1:
                raise ConnectionResetError('reset')
    path=tmp_path/'journal.jsonl'
    with path.open('w+') as stream:
        budget=module.AttemptBudget(2,stream)
        first=module.CountedConnection(Connection(),budget)
        second=module.CountedConnection(Connection(),budget)
        with pytest.raises(ConnectionResetError):first.request('POST','/chat',body=b'first')
        second.request('POST','/chat',body=b'second')
        with pytest.raises(RuntimeError,match='budget'):second.request('POST','/chat',body=b'third')
    assert len(calls)==2 and budget.used==2
    rows=[json.loads(x) for x in path.read_text().splitlines()]
    assert len(rows)==2 and [r['sequence'] for r in rows]==[1,2]
    assert 'first' not in path.read_text()  # Only payload hash, no body or headers.


def test_reservation_is_persisted_before_network_send(tmp_path):
    path=tmp_path/'journal.jsonl'
    class Connection:
        def request(self,*args,**kwargs):
            assert json.loads(path.read_text())['kind']=='request_reserved'
    with path.open('w+') as stream:
        module.CountedConnection(Connection(),module.AttemptBudget(1,stream)).request('POST','/chat',body=b'payload')


@pytest.mark.parametrize('content',['garbled','{"label":', '{}','{"label":3}','{"label":true}','{"label":"1"}'])
def test_no_garbled_or_missing_label_is_silently_scored_as_neutral(content):
    with pytest.raises((ValueError,json.JSONDecodeError)):
        module.strict_label(content)


def test_strict_label_accepts_complete_json_and_complete_fences():
    assert module.strict_label('{"label":0}')==0
    assert module.strict_label('```json\n{"label":2}\n```')==2
    assert module.strict_label('<|inner_prefix|>text<|inner_suffix|>{"label":1}')==1
