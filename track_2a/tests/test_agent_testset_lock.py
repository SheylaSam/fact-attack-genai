"""Testset lock and loader: review status, `lock` (manifest with hashes), `load_testset` (dev free, test closed) and
`lock_testset.py`. The cases and the vote catalog are the in-memory fakes of `test_agent_testset.py`; `lock` runs with
`complete=False` on a mini set. No PDF, model or API is needed."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx")  # requirements-agent.txt: the testset reuses the graph's outcome names and page reader
pytest.importorskip("langgraph")

from src.agent import testset
from src.agent.testset import (LockRefused, ReviewStatus, case_sha, load_testset, lock, read_cases, read_review,
                               review_status)
from tests.test_agent_testset import QUOTE_DEFAULT, QUOTE_WRAPPED, V_EID, V_NEW, VOTES, case, line_of, special

spec = importlib.util.spec_from_file_location(
    "lock_testset", Path(__file__).parents[1] / "scripts" / "lock_testset.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

NOW = datetime(2026, 11, 8, 10, 30, 15, 123456, tzinfo=timezone.utc)
T0 = datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc)

# A mini set: every split and group once or twice, valid for `validate(..., complete=False)`
DEV_1 = case()
DEV_2 = case(id="dev-002", claim="Der Bund zahlt jedes Jahr mehr an die Wohnungsförderung der Kantone.",
             expected="widersprochen", page=5, quote=QUOTE_WRAPPED)
SDEV_1 = special("mehrdeutig")
SDEV_4 = special("ohne_abstimmung")
TEST_1 = case(id="test-001", split="test", vote_id=V_NEW.vote_id, novelty="ungesehen", page=6, quote=QUOTE_DEFAULT,
              claim="Der Verkauf von Feuerwerk an Private wird durch den Bund stark eingeschränkt.")
STEST_1 = special("api_ausfall", id="stest-01", split="test", vote_id=V_EID.vote_id, page=3, quote=QUOTE_DEFAULT,
                  claim="Wer die E-ID beantragt, erhält zuerst eine Bestätigung per Post.")
MINI = [DEV_1, DEV_2, SDEV_1, SDEV_4, TEST_1, STEST_1]
BOOKLETS = ["2020-02-09_de.pdf", "2021-03-07_de.pdf", "2022-09-25_de.pdf", "2024-06-09_de.pdf", "2026-11-29_de.pdf"]
SOURCES = {name: {"date": name[:10], "language": "de", "sha256": f"{number:064x}"}
           for number, name in enumerate(BOOKLETS, 1)}  # 2022-09-25 is referenced by no case


def stamp(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def judge(item, minutes=0, **changes):
    """The judgement event that agrees with the case (`after_answer` only for an ambiguous case)."""
    event = {"id": item.id, "case_sha": case_sha(item), "expected": item.expected,
             "after_answer": item.after_answer if item.kind == "mehrdeutig" else None, "note": "",
             "at": stamp(minutes)}
    event.update(changes)
    return event


def judgements(cases=MINI):
    return [judge(item, number) for number, item in enumerate(cases)]


def by_id(events):
    review = {}
    for event in events:
        review.setdefault(event["id"], []).append(event)
    return review


def write_review(path, events):
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")


@pytest.fixture
def files(tmp_path):
    return SimpleNamespace(cases=tmp_path / "cases.jsonl", review=tmp_path / "label_review.jsonl",
                           manifest=tmp_path / "manifest.json")


def prepare(files, cases=MINI, events=None):
    files.cases.write_text("".join(line_of(item) + "\n" for item in cases), encoding="utf-8")
    write_review(files.review, judgements(cases) if events is None else events)


def do_lock(files, **changes):
    options = dict(votes=VOTES, sources=SOURCES, cases_path=files.cases, review_path=files.review,
                   manifest_path=files.manifest, git_commit="abc123", git_dirty=False, now=NOW, complete=False)
    options.update(changes)
    return lock(**options)


def refusal(files, **changes):
    with pytest.raises(LockRefused) as error:
        do_lock(files, **changes)
    assert not files.manifest.exists()  # a refusal writes nothing
    return error.value


def test_mini_set_is_valid():
    assert [item.id for item in MINI] == ["dev-001", "dev-002", "sdev-01", "sdev-04", "test-001", "stest-01"]
    assert testset.validate(MINI, VOTES, complete=False) == []


# --- review status ---------------------------------------------------------------------------------------------------

def test_latest_judgement_counts():
    wrong, right = judge(DEV_1, 1, expected="widersprochen"), judge(DEV_1, 2)
    assert review_status([DEV_1], by_id([wrong, right])).disagree == []  # the last one agrees
    assert review_status([DEV_1], by_id([right, judge(DEV_1, 3, expected="widersprochen")])).disagree == ["dev-001"]


def test_latest_is_decided_by_time_and_file_order_breaks_ties():
    early_wrong, late_right = judge(DEV_1, 1, expected="widersprochen"), judge(DEV_1, 5)
    status = review_status([DEV_1], {"dev-001": [late_right, early_wrong]})  # listed in the wrong order
    assert status.disagree == [] and status.stale == []
    same_time = [judge(DEV_1, 4, expected="widersprochen"), judge(DEV_1, 4)]
    assert review_status([DEV_1], {"dev-001": same_time}).disagree == []
    assert review_status([DEV_1], {"dev-001": same_time[::-1]}).disagree == ["dev-001"]


def test_ambiguous_case_compares_after_answer_but_other_cases_do_not():
    status = review_status([SDEV_1], by_id([judge(SDEV_1, after_answer="widersprochen")]))
    assert status.disagree == ["sdev-01"]
    status = review_status([SDEV_1], by_id([judge(SDEV_1, after_answer=None)]))
    assert status.disagree == ["sdev-01"]
    assert review_status([SDEV_1], by_id([judge(SDEV_1)])).disagree == []
    assert review_status([DEV_1], by_id([judge(DEV_1, after_answer="widersprochen")])) == ReviewStatus(
        missing=[], stale=[], disagree=[], first_agree=1, changed_after_first_review=[])


def test_status_names_missing_stale_and_disagreeing_cases_in_case_order():
    old = replace(DEV_2, note="Entwurf")
    review = by_id([judge(old, 1), judge(DEV_1, 2, expected="nicht_belegt"), judge(SDEV_1, 3)])
    status = review_status(MINI, review)
    assert status.missing == ["sdev-04", "test-001", "stest-01"]
    assert status.stale == ["dev-002"]
    assert status.disagree == ["dev-001"]


def test_first_agree_and_changed_after_first_review():
    changed = replace(TEST_1, note="neu")  # the judgement below was given for TEST_1 as it was before
    review = by_id([
        judge(DEV_1, 1),                                            # first judgement agrees
        judge(DEV_2, 1, expected="gestuetzt"), judge(DEV_2, 2),     # first differs, corrected judgement agrees
        judge(SDEV_1, 1, after_answer="widersprochen"), judge(SDEV_1, 2),  # same for the answer of an ambiguous case
        judge(TEST_1, 1), judge(changed, 2),                        # the case changed after the first judgement
    ])
    status = review_status([DEV_1, DEV_2, SDEV_1, changed, STEST_1], review)
    assert status == ReviewStatus(missing=["stest-01"], stale=[], disagree=[], first_agree=1,
                                  changed_after_first_review=["test-001"])
    assert isinstance(status.first_agree, int)


def test_first_agree_needs_the_first_judgement_for_the_current_case():
    changed = replace(DEV_1, note="neu")
    status = review_status([changed], by_id([judge(DEV_1, 1), judge(changed, 2)]))  # old version agreed, so would new
    assert status.first_agree == 0 and status.changed_after_first_review == ["dev-001"]


def test_review_status_ignores_judgements_of_unknown_ids():
    status = review_status([DEV_1], by_id([judge(DEV_1), judge(DEV_2)]))
    assert status == ReviewStatus(missing=[], stale=[], disagree=[], first_agree=1, changed_after_first_review=[])


# --- reading the review export ---------------------------------------------------------------------------------------

def test_read_review_groups_by_id_and_sorts_by_time(tmp_path):
    path = tmp_path / "label_review.jsonl"
    late, early, other = judge(DEV_1, 9), judge(DEV_1, 2, expected="widersprochen"), judge(DEV_2, 4)
    write_review(path, [late, other, early])
    review = read_review(path)
    assert list(review) == ["dev-001", "dev-002"]
    assert review["dev-001"] == [early, late] and review["dev-002"] == [other]


def test_read_review_keeps_file_order_for_equal_times(tmp_path):
    path = tmp_path / "label_review.jsonl"
    first, second = judge(DEV_1, 2, note="eins"), judge(DEV_1, 2, note="zwei")
    write_review(path, [first, second])
    assert [event["note"] for event in read_review(path)["dev-001"]] == ["eins", "zwei"]


def test_read_review_skips_blank_lines_and_keeps_unicode_notes(tmp_path):
    path = tmp_path / "label_review.jsonl"
    path.write_text("\n" + json.dumps(judge(DEV_1, note="Büchlein sagt «Ja»"), ensure_ascii=False) + "\n\n",
                    encoding="utf-8")
    assert read_review(path)["dev-001"][0]["note"] == "Büchlein sagt «Ja»"


def test_read_review_names_the_bad_line(tmp_path):
    path = tmp_path / "label_review.jsonl"
    good = json.dumps(judge(DEV_1))
    broken = json.loads(good)
    del broken["case_sha"]
    for text, fragment in [("{kaputt", "Zeile 2"), ("[1]", "Zeile 2"), (json.dumps(broken), "case_sha"),
                           (json.dumps({**judge(DEV_1), "expected": 3}), "expected"),
                           (json.dumps({**judge(DEV_1), "after_answer": 5}), "after_answer"),
                           (json.dumps({**judge(DEV_1), "at": None}), "at muss")]:
        path.write_text(good + "\n" + text + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Zeile 2") as error:
            read_review(path)
        assert fragment in str(error.value)


# --- lock ------------------------------------------------------------------------------------------------------------

def test_lock_writes_manifest_and_refuses_relock(files):
    prepare(files)
    manifest = do_lock(files)
    text = files.manifest.read_text(encoding="utf-8")
    assert json.loads(text) == manifest
    assert text.startswith('{\n  "schema": 1,\n') and text.endswith("}\n")
    assert manifest["schema"] == 1
    assert manifest["locked_at"] == "2026-11-08T10:30:15Z"
    assert manifest["git_commit"] == "abc123" and manifest["git_dirty"] is False
    assert manifest["cases_sha256"] == hashlib.sha256(files.cases.read_bytes()).hexdigest()
    assert manifest["review_sha256"] == hashlib.sha256(files.review.read_bytes()).hexdigest()
    assert manifest["counts"] == {
        "split_group": {"dev/haupt": 2, "dev/spezial": 2, "test/haupt": 1, "test/spezial": 1},
        "kind": {"api_ausfall": 1, "keine": 3, "mehrdeutig": 1, "ohne_abstimmung": 1},
        "novelty": {"bekannt": 4, "keine": 1, "ungesehen": 1},
        "expected": {"gestuetzt": 2, "quelle_fehlt": 1, "rueckfrage": 1, "technischer_fehler": 1, "widersprochen": 1}}
    assert manifest["booklets"] == {name: SOURCES[name]["sha256"] for name in BOOKLETS if name != "2022-09-25_de.pdf"}
    assert manifest["review"] == {"cases": 6, "first_judgement_agrees": 6, "changed_after_first_review": [],
                                  "reviewer": "Sheyla"}
    assert manifest["usage"] == "Dev frei. Test und Spezial-Test einmalig nach dem Konfigurationsfreeze (Stufe 5)."
    assert list(manifest) == ["schema", "locked_at", "git_commit", "git_dirty", "cases_sha256", "review_sha256",
                              "counts", "booklets", "review", "usage"]
    for _ in range(2):  # the second call is refused as well, and nothing changes
        with pytest.raises(LockRefused) as error:
            do_lock(files, now=NOW + timedelta(days=1), git_commit="other")
        assert error.value.problems == ["bereits gesperrt; Änderungen brauchen ein neues Testset"]
        assert files.manifest.read_text(encoding="utf-8") == text


def test_lock_refuses_before_reading_anything_when_locked(files):
    files.manifest.write_text("{}", encoding="utf-8")  # no cases, no review: still "already locked", nothing else
    with pytest.raises(LockRefused) as error:
        do_lock(files)
    assert error.value.problems == ["bereits gesperrt; Änderungen brauchen ein neues Testset"]
    assert files.manifest.read_text(encoding="utf-8") == "{}"


def test_lock_never_overwrites_a_manifest_that_appears_in_the_meantime(files, monkeypatch):
    prepare(files)
    read_inputs = testset._read_inputs

    def racing(*args):
        files.manifest.write_text("von jemand anderem\n", encoding="utf-8")  # locked by a second process
        return read_inputs(*args)

    monkeypatch.setattr(testset, "_read_inputs", racing)
    with pytest.raises(LockRefused) as error:
        do_lock(files)
    assert error.value.problems == ["bereits gesperrt; Änderungen brauchen ein neues Testset"]
    assert files.manifest.read_text(encoding="utf-8") == "von jemand anderem\n"


def test_lock_records_dirty_tree_and_utc_time(files):
    prepare(files)
    berlin = timezone(timedelta(hours=1))
    manifest = do_lock(files, git_dirty=True, now=datetime(2026, 11, 8, 11, 30, 15, tzinfo=berlin))
    assert manifest["git_dirty"] is True and manifest["locked_at"] == "2026-11-08T10:30:15Z"


def test_lock_needs_an_aware_time(files):
    prepare(files)
    with pytest.raises(ValueError, match="Zeitzone"):
        do_lock(files, now=datetime(2026, 11, 8, 10, 30, 15))
    assert not files.manifest.exists()


def test_lists_booklets_of_ambiguous_votes_too(files):
    ambiguous = special("mehrdeutig", id="stest-01", split="test", vote_id=V_EID.vote_id,
                        ambiguous_with=(V_NEW.vote_id,), page=3, quote=QUOTE_DEFAULT)
    prepare(files, [ambiguous])
    manifest = do_lock(files)
    assert list(manifest["booklets"]) == ["2021-03-07_de.pdf", "2026-11-29_de.pdf"]
    assert manifest["counts"]["split_group"] == {"test/spezial": 1}


def test_stale_review_blocks_lock(files):
    old = replace(DEV_2, note="Entwurf vor der Durchsicht")  # Review Focus 3: the case changed after the judgement
    events = [judge(old, 1) if item.id == "dev-002" else judge(item, 1) for item in MINI]
    prepare(files, events=events)
    problems = refusal(files).problems
    assert len(problems) == 1 and "dev-002" in problems[0] and "geändert" in problems[0]
    assert "dev-001" not in problems[0]


def test_stale_case_is_not_also_reported_as_disagreement(files):
    old = replace(DEV_2, note="Entwurf", expected="gestuetzt")  # judged as another version with another label
    prepare(files, events=[judge(old, 0, expected="gestuetzt")] + judgements()[:1] + judgements()[2:])
    problems = refusal(files).problems
    assert len(problems) == 1 and "dev-002" in problems[0]


def test_disagreement_blocks_lock(files):
    events = judgements()
    events[0] = judge(DEV_1, 0, expected="nicht_belegt")
    events[2] = judge(SDEV_1, 2, after_answer="nicht_belegt")
    prepare(files, events=events)
    problems = refusal(files).problems
    assert len(problems) == 1 and "dev-001" in problems[0] and "sdev-01" in problems[0]
    assert "weicht" in problems[0]


def test_missing_review_blocks_lock(files):
    prepare(files, events=[event for event in judgements() if event["id"] not in ("dev-002", "stest-01")])
    problems = refusal(files).problems
    assert len(problems) == 1 and "dev-002" in problems[0] and "stest-01" in problems[0]
    assert "Urteil" in problems[0]


def test_review_file_missing_or_broken_blocks_lock(files):
    prepare(files)
    files.review.unlink()
    problems = refusal(files).problems
    assert len(problems) == 1 and str(files.review) in problems[0]
    files.review.write_text("{kaputt\n", encoding="utf-8")
    assert "Zeile 1" in refusal(files).problems[0]


def test_cases_file_missing_or_broken_blocks_lock(files):
    prepare(files)
    files.cases.write_text(line_of(DEV_1) + "\n{kaputt\n", encoding="utf-8")
    assert "Zeile 2" in refusal(files).problems[0]
    files.cases.unlink()
    problems = refusal(files).problems
    assert len(problems) == 1 and str(files.cases) in problems[0]


def test_invalid_case_blocks_lock_with_the_validator_message(files):
    broken = replace(DEV_2, quote="Das steht so nicht im Büchlein, nirgends.")
    prepare(files, [DEV_1, broken])
    problems = refusal(files).problems
    assert len(problems) == 1 and problems[0].startswith("dev-002:") and "nicht wörtlich" in problems[0]


def test_lock_collects_all_problems_at_once(files):
    broken = replace(DEV_2, quote="Das steht so nicht im Büchlein, nirgends.")
    prepare(files, [DEV_1, broken], events=[judge(DEV_1, 0, expected="nicht_belegt")])
    problems = refusal(files).problems
    assert len(problems) == 3
    assert problems[0].startswith("dev-002:")
    assert any("dev-001" in p and "weicht" in p for p in problems)
    assert any("dev-002" in p and "Ohne Urteil" in p for p in problems)


def test_lock_checks_the_counts_of_the_finished_set_by_default(files):
    prepare(files)
    problems = refusal(files, complete=True).problems
    assert problems and all(p.startswith("Testset:") for p in problems)
    assert "Testset: dev/haupt hat 2 Fälle, erwartet 30" in problems
    assert not files.manifest.exists()


def test_lock_passes_the_earlier_claims_to_the_validator(files):
    prepare(files)
    problems = refusal(files, previous_claims=[DEV_1.claim]).problems
    assert len(problems) == 1 and problems[0].startswith("dev-001:") and "früheren" in problems[0]


def test_lock_refuses_a_booklet_that_is_not_in_the_catalog(files):
    prepare(files)
    sources = {name: SOURCES[name] for name in BOOKLETS if name != "2026-11-29_de.pdf"}
    problems = refusal(files, sources=sources).problems
    assert len(problems) == 1 and "2026-11-29_de.pdf" in problems[0]


def test_lock_counts_changed_cases_in_the_manifest(files):
    changed = replace(DEV_2, note="Wortlaut nach der ersten Durchsicht geschärft")
    events = [judge(DEV_2, 0, case_sha=case_sha(DEV_2)) if item.id == "dev-002" else judge(item, 0) for item in MINI]
    events.append(judge(changed, 5))  # the first judgement of dev-002 was for an older version
    prepare(files, [changed if item.id == "dev-002" else item for item in MINI], events=events)
    review = do_lock(files)["review"]
    assert review == {"cases": 6, "first_judgement_agrees": 5, "changed_after_first_review": ["dev-002"],
                      "reviewer": "Sheyla"}


# --- load_testset ----------------------------------------------------------------------------------------------------

def locked(files):
    prepare(files)
    do_lock(files)
    return dict(cases_path=files.cases, manifest_path=files.manifest)


def test_load_dev_after_lock(files):
    paths = locked(files)
    dev = load_testset("dev", **paths)
    assert [item.id for item in dev] == ["dev-001", "dev-002", "sdev-01", "sdev-04"]  # main and special cases
    assert dev == [item for item in read_cases(files.cases) if item.split == "dev"]
    assert load_testset("dev", allow_test=True, **paths) == dev  # allow_test opens nothing more for dev


def test_load_refuses_unlocked(files):
    prepare(files)
    with pytest.raises(ValueError, match="Testset ist nicht gesperrt"):
        load_testset("dev", cases_path=files.cases, manifest_path=files.manifest)
    with pytest.raises(ValueError, match="Testset ist nicht gesperrt"):
        load_testset("test", cases_path=files.cases, manifest_path=files.manifest, allow_test=True)


def test_load_refuses_changed_cases(files):  # Review Focus 5
    paths = locked(files)
    files.cases.write_text(files.cases.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # one byte more
    for split, extra in (("dev", {}), ("test", {"allow_test": True})):
        with pytest.raises(ValueError, match="cases.jsonl wurde nach dem Sperren verändert"):
            load_testset(split, **paths, **extra)


def test_load_refuses_a_relabelled_case(files):
    paths = locked(files)
    text = files.cases.read_text(encoding="utf-8")
    files.cases.write_text(text.replace('"expected": "gestuetzt"', '"expected": "widersprochen"', 1), encoding="utf-8")
    assert files.cases.read_text(encoding="utf-8") != text
    with pytest.raises(ValueError, match="verändert"):
        load_testset("dev", **paths)


def test_load_test_needs_allow_test(files):  # Review Focus 5
    paths = locked(files)
    with pytest.raises(PermissionError, match=r"Testteil erst nach dem Konfigurationsfreeze \(allow_test=True\)"):
        load_testset("test", **paths)
    with pytest.raises(PermissionError):
        load_testset("test", allow_test=False, **paths)
    test = load_testset("test", allow_test=True, **paths)
    assert [item.id for item in test] == ["test-001", "stest-01"]


def test_load_rejects_an_unknown_split(files):
    paths = locked(files)
    for split in ("train", "", "Dev", "all"):
        with pytest.raises(ValueError, match="Split"):
            load_testset(split, allow_test=True, **paths)


def test_load_rejects_an_unreadable_manifest(files):
    prepare(files)
    for text in ("{kaputt", "[1]", "{}", '{"schema": 1}'):
        files.manifest.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="Manifest"):
            load_testset("dev", cases_path=files.cases, manifest_path=files.manifest)


def test_load_uses_the_default_paths_of_the_testset_directory():
    import inspect
    parameters = inspect.signature(load_testset).parameters
    assert parameters["cases_path"].default == testset.CASES_PATH
    assert parameters["manifest_path"].default == testset.MANIFEST_PATH
    allow_test = parameters["allow_test"]
    assert allow_test.default is False and allow_test.kind is allow_test.KEYWORD_ONLY
    lock_parameters = inspect.signature(lock).parameters
    assert [lock_parameters[name].default for name in ("cases_path", "review_path", "manifest_path", "complete")] == [
        testset.CASES_PATH, testset.REVIEW_PATH, testset.MANIFEST_PATH, True]


# --- lock_testset.py -----------------------------------------------------------------------------------------------

@pytest.fixture
def run(monkeypatch, files, tmp_path):
    """The script on the mini set: its paths in tmp_path, a fake engine, catalog and git state."""
    prepare(files)
    export = tmp_path / "export" / "label_review.jsonl"
    export.parent.mkdir()
    write_review(export, judgements())
    files.review.unlink()
    state = SimpleNamespace(export=export, engine_closed=False, lock_options=None)

    class Engine:
        def close(self):
            state.engine_closed = True

    def partial_lock(**options):  # the mini set is no finished set; the default `complete=True` is tested below
        state.lock_options = options
        return testset.lock(**{**options, "complete": False})

    monkeypatch.setattr(script, "CASES_PATH", files.cases)
    monkeypatch.setattr(script, "REVIEW_PATH", files.review)
    monkeypatch.setattr(script, "MANIFEST_PATH", files.manifest)
    monkeypatch.setattr(script, "make_engine", Engine)
    monkeypatch.setattr(script, "load_catalog", lambda path: SOURCES)
    monkeypatch.setattr(script, "load_vote_catalog", lambda sources, directory, engine: VOTES)
    monkeypatch.setattr(script, "load_previous_claims", lambda: ["Ganz etwas anderes behauptet dieser Satz."])
    monkeypatch.setattr(script, "git_state", lambda: ("deadbeef", True))
    monkeypatch.setattr(script, "lock", partial_lock)
    return state


def test_script_copies_the_export_locks_and_prints_the_summary(run, files, capsys):
    assert script.main(["--review", str(run.export)]) == 0
    out = capsys.readouterr().out
    assert files.review.read_bytes() == run.export.read_bytes()
    manifest = json.loads(files.manifest.read_text(encoding="utf-8"))
    assert manifest["git_commit"] == "deadbeef" and manifest["git_dirty"] is True
    assert manifest["review_sha256"] == hashlib.sha256(run.export.read_bytes()).hexdigest()
    assert datetime.strptime(manifest["locked_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert "gesperrt" in out and "6 Fälle" in out and "deadbeef" in out and manifest["cases_sha256"] in out
    assert "Arbeitsbaum" in out  # says that the tree was not clean
    assert run.engine_closed
    assert run.lock_options["previous_claims"] == ["Ganz etwas anderes behauptet dieser Satz."]
    assert run.lock_options["votes"] is VOTES and run.lock_options["sources"] is SOURCES
    assert "complete" not in run.lock_options  # the default (True) applies


def test_script_without_review_option_uses_the_stored_review(run, files, capsys):
    files.review.write_bytes(run.export.read_bytes())
    assert script.main([]) == 0
    assert files.manifest.exists() and "gesperrt" in capsys.readouterr().out


def test_script_does_not_copy_a_file_onto_itself(run, files):
    files.review.write_bytes(run.export.read_bytes())
    assert script.main(["--review", str(files.review)]) == 0
    assert files.review.read_bytes() == run.export.read_bytes()


def test_script_prints_the_problems_and_exits_with_1(run, files, capsys):
    events = judgements()
    events[0] = judge(DEV_1, 0, expected="nicht_belegt")
    write_review(run.export, events)
    assert script.main(["--review", str(run.export)]) == 1
    out = capsys.readouterr().out
    assert "dev-001" in out and "weicht" in out and "Testset gesperrt" not in out
    assert not files.manifest.exists()


def test_script_refuses_an_already_locked_set_without_touching_the_review(run, files, capsys):
    files.review.write_text("alter Stand\n", encoding="utf-8")
    files.manifest.write_text('{"schema": 1}\n', encoding="utf-8")
    assert script.main(["--review", str(run.export)]) == 1
    assert "bereits gesperrt" in capsys.readouterr().out
    assert files.review.read_text(encoding="utf-8") == "alter Stand\n"  # the locked review is not overwritten
    assert files.manifest.read_text(encoding="utf-8") == '{"schema": 1}\n'
    assert run.lock_options is None  # not even tried


def test_script_reports_a_missing_review_file(run, files, tmp_path, capsys):
    assert script.main(["--review", str(tmp_path / "fehlt.jsonl")]) == 1
    assert str(tmp_path / "fehlt.jsonl") in capsys.readouterr().out
    assert script.main([]) == 1  # no stored review either
    assert "label_review.jsonl" in capsys.readouterr().out
    assert not files.manifest.exists()


def test_script_checks_the_counts_of_the_finished_set(run, files, capsys, monkeypatch):
    monkeypatch.setattr(script, "lock", testset.lock)  # the real default: complete=True
    assert script.main(["--review", str(run.export)]) == 1
    assert "Testset: dev/haupt hat 2 Fälle, erwartet 30" in capsys.readouterr().out
    assert not files.manifest.exists()


def test_script_reports_a_missing_cases_file(run, files, capsys):
    files.cases.unlink()
    assert script.main(["--review", str(run.export)]) == 1
    assert f"Keine Fälle: {files.cases} fehlt" in capsys.readouterr().out
    assert not files.manifest.exists() and run.lock_options is None


def test_script_closes_the_engine_on_refusal(run, files, capsys):
    write_review(run.export, [])  # no judgement at all: every case is open
    assert script.main(["--review", str(run.export)]) == 1
    assert run.engine_closed


# --- lock_testset.py --status (read-only) ---------------------------------------------------------------------------

def snapshot(directory):
    """Name -> bytes of every file under `directory`: to prove that a command wrote nothing."""
    return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*")) if path.is_file()}


def test_status_prints_the_counts_and_writes_nothing(run, files, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert script.main(["--status", "--review", str(run.export)]) == 0
    out = capsys.readouterr().out
    assert "6 Fälle" in out and "Fehlend: keine" in out and "Veraltet: keine" in out and "Abweichend: keine" in out
    assert "Erstes Urteil stimmte überein: 6 von 6" in out and "Bereit zum Sperren" in out
    assert snapshot(tmp_path) == before  # no copy of the export, no manifest, nothing
    assert not files.review.exists() and not files.manifest.exists()
    assert run.lock_options is None and not run.engine_closed  # the engine is not even started


def test_status_names_missing_stale_and_disagreeing_cases(run, files, tmp_path, capsys):
    events = judgements()
    events[0] = judge(DEV_1, 0, expected="nicht_belegt")  # disagrees
    events[1] = judge(DEV_2, 1, case_sha="0" * 64)  # given for another version of the case
    del events[3]  # sdev-04 has no judgement
    write_review(run.export, events)
    before = snapshot(tmp_path)
    assert script.main(["--status", "--review", str(run.export)]) == 1
    out = capsys.readouterr().out
    assert "Fehlend (1): sdev-04" in out
    assert "Veraltet (1): dev-002" in out
    assert "Abweichend (1): dev-001" in out
    assert "Beurteilt, stimmt überein: 3" in out and "Noch nicht bereit zum Sperren" in out
    assert "Bereit zum Sperren" not in out.replace("Noch nicht bereit zum Sperren", "")
    assert snapshot(tmp_path) == before and not files.manifest.exists()


def test_status_counts_the_blind_agreement_and_the_changed_cases(run, files, capsys):
    events = judgements()
    events.insert(0, judge(DEV_1, -5, case_sha="1" * 64))  # the first judgement of dev-001 was for an older version
    write_review(run.export, events)
    assert script.main(["--status", "--review", str(run.export)]) == 0
    out = capsys.readouterr().out
    assert "Erstes Urteil stimmte überein: 5 von 6" in out
    assert "Nach dem ersten Urteil geändert (1): dev-001" in out


def test_status_without_review_option_reads_the_stored_review(run, files, capsys):
    files.review.write_bytes(run.export.read_bytes())
    stored = files.review.read_bytes()
    assert script.main(["--status"]) == 0
    assert str(files.review) in capsys.readouterr().out
    assert files.review.read_bytes() == stored and not files.manifest.exists()


def test_status_is_available_after_the_lock_and_changes_nothing(run, files, capsys):
    assert script.main(["--review", str(run.export)]) == 0
    capsys.readouterr()
    locked = {name: path.read_bytes() for name, path in (("review", files.review), ("manifest", files.manifest))}
    assert script.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "bereits gesperrt" in out and "6 Fälle" in out
    assert {name: path.read_bytes() for name, path in (("review", files.review), ("manifest", files.manifest))} == locked


def test_status_reports_a_missing_review_file_or_cases_file(run, files, tmp_path, capsys):
    assert script.main(["--status", "--review", str(tmp_path / "fehlt.jsonl")]) == 1
    assert f"Keine Review: {tmp_path / 'fehlt.jsonl'} fehlt" in capsys.readouterr().out
    assert script.main(["--status"]) == 1  # no stored review either
    assert "label_review.jsonl" in capsys.readouterr().out
    files.cases.unlink()
    assert script.main(["--status", "--review", str(run.export)]) == 1
    assert f"Keine Fälle: {files.cases} fehlt" in capsys.readouterr().out
    assert not files.manifest.exists() and not files.review.exists()


def test_status_names_the_bad_line_of_the_review(run, files, capsys):
    run.export.write_text(run.export.read_text(encoding="utf-8") + "{kaputt\n", encoding="utf-8")
    assert script.main(["--status", "--review", str(run.export)]) == 1
    assert "Zeile 7" in capsys.readouterr().out and not files.manifest.exists()


def test_status_ignores_judgements_of_unknown_ids(run, files, capsys):
    stray = dict(judge(DEV_1, 9), id="dev-099")
    write_review(run.export, judgements() + [stray])
    assert script.main(["--status", "--review", str(run.export)]) == 0
    assert "dev-099" not in capsys.readouterr().out


def test_status_and_lock_options_stay_apart():
    assert script.build_parser().parse_args(["--status"]).status is True
    assert script.build_parser().parse_args([]).status is False


# --- git state of the script ---------------------------------------------------------------------------------------

def test_changed_paths_reads_porcelain_z_output():
    status = " M track_2a/src/a.py\0?? track_2a/data/genai/testset/label_review.jsonl\0R  neu.py\0alt.py\0A  b/c d.py\0"
    assert script.changed_paths(status) == ["track_2a/src/a.py", "track_2a/data/genai/testset/label_review.jsonl",
                                            "neu.py", "alt.py", "b/c d.py"]
    assert script.changed_paths("") == []


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                           "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def test_git_state_ignores_the_testset_directory(tmp_path):
    repo = tmp_path / "repo"
    (repo / "track_2a" / "data" / "genai" / "testset").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "track_2a" / "data" / "genai" / "testset" / "cases.jsonl").write_text("a\n", encoding="utf-8")
    (repo / "readme.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "start")
    head = git(repo, "rev-parse", "HEAD").strip()
    prefix = "track_2a/data/genai/testset/"
    assert script.git_state(repo, prefix) == (head, False)
    testset_dir = repo / "track_2a" / "data" / "genai" / "testset"
    (testset_dir / "label_review.jsonl").write_text("new\n", encoding="utf-8")  # untracked, in the testset directory
    (testset_dir / "cases.jsonl").write_text("b\n", encoding="utf-8")           # modified, in the testset directory
    assert script.git_state(repo, prefix) == (head, False)
    (repo / "notes.txt").write_text("loose\n", encoding="utf-8")                # untracked, elsewhere
    assert script.git_state(repo, prefix) == (head, True)
    (repo / "notes.txt").unlink()
    (repo / "readme.md").write_text("changed\n", encoding="utf-8")              # tracked and modified, elsewhere
    assert script.git_state(repo, prefix) == (head, True)


def test_git_state_names_the_problem_outside_a_repository(tmp_path):
    with pytest.raises(RuntimeError, match="git"):
        script.git_state(tmp_path, "x/")
