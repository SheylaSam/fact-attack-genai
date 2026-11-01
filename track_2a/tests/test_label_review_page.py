"""Blind label review page: the HTML the testset reviewer opens (`build_label_review.py`). The page's behaviour (keys,
blind labels, storage) runs in the browser; these tests pin what can be checked without one: the data in the page, its
escaping, that it loads nothing external, and the script's CLI around it. No PDF, model or API is needed."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

pytest.importorskip("httpx")  # requirements-agent.txt: the testset reuses the graph's outcome names and page reader
pytest.importorskip("langgraph")

from src import config
from src.agent.source_index import VoteRef
from src.agent.testset import FAULTS, KINDS, OUTCOMES, VERDICTS, EvalCase, case_sha, read_cases

spec = importlib.util.spec_from_file_location(
    "build_label_review", Path(__file__).parents[1] / "scripts" / "build_label_review.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
render_review_page = script.render_review_page

V_DEV = VoteRef("2020-02-09_de#1", "2020-02-09_de", "2020-02-09", "de", "Bezahlbare Wohnungen", (4, 5, 6))
V_NEW = VoteRef("2026-11-29_de#2", "2026-11-29_de", "2026-11-29", "de", "Feuerwerk", (6, 7))


def case(**changes):
    base = dict(id="dev-001", split="dev", group="haupt", kind=None, novelty="bekannt",
                claim="Die Gebühr für die Wohnungsförderung steigt auf 335 Franken im Jahr.",
                expected="gestuetzt", acceptable=(), vote_id=V_DEV.vote_id, ambiguous_with=(), after_answer=None,
                page=4, quote="Die Gebühr steigt auf 335 Franken pro Jahr.", fault=None, note="")
    base.update(changes)
    return EvalCase(**base)


def view(page=4, **changes):
    base = dict(vote_title="Bezahlbare Wohnungen", vote_date="2020-02-09", page=page,
                page_text="Seite 4\nDie Gebühr steigt auf 335 Franken pro Jahr.\n",
                pdf_href=f"file:///Users/test/booklets/2020-02-09_de.pdf#page={page}")
    base.update(changes)
    return base


CASES = [
    case(),
    case(id="sdev-01", group="spezial", kind="mehrdeutig", expected="rueckfrage", acceptable=("rueckfrage",),
         ambiguous_with=("2024-11-24_de#3",), after_answer="gestuetzt", note="Zwei Vorlagen passen."),
    case(id="sdev-02", group="spezial", kind="api_ausfall", expected="technischer_fehler",
         acceptable=("technischer_fehler",), fault="timeout", note="Der Ausfall wird eingespielt."),
    case(id="sdev-03", group="spezial", kind="ohne_abstimmung", expected="quelle_fehlt", acceptable=("quelle_fehlt",),
         novelty=None, vote_id=None, page=None, quote=None, note="Keine Vorlage handelt davon."),
]
VIEWS = {"dev-001": view(), "sdev-01": view(), "sdev-02": view(), "sdev-03": {}}


def page_data(html):
    """The JSON of the page's data block, parsed like the browser does."""
    match = re.search(r'<script type="application/json" id="review-data">(.*?)</script>', html, re.S)
    assert match, "no data block"
    return json.loads(match.group(1))


def code_of(html):
    """The page's own script (the last <script>)."""
    return re.findall(r"<script>(.*?)</script>", html, re.S)[-1]


# --- the data in the page -------------------------------------------------------------------------------------------

def test_page_contains_every_case_and_sha():
    html = render_review_page(CASES, VIEWS)
    for each in CASES:
        assert each.id in html and case_sha(each) in html


def test_page_data_carries_the_cases_their_views_and_the_export_contract():
    data = page_data(render_review_page(CASES, VIEWS))
    assert [entry["id"] for entry in data["cases"]] == [each.id for each in CASES]
    first = data["cases"][0]
    assert first["sha"] == case_sha(CASES[0]) and first["expected"] == "gestuetzt"
    assert first["view"]["pdf_href"].endswith("#page=4") and first["view"]["page_text"].startswith("Seite 4")
    assert data["cases"][3]["view"] == {} and data["cases"][3]["quote"] is None
    assert data["storageKey"] == "fact-attack-testset-review-v1"
    assert data["eventFields"] == ["id", "case_sha", "expected", "after_answer", "note", "at"]


def test_case_without_a_view_still_renders():
    data = page_data(render_review_page([case()], {}))
    assert data["cases"][0]["view"] == {}


def test_empty_case_list_renders():
    html = render_review_page([], {})
    assert page_data(html)["cases"] == [] and "<html" in html


def test_same_input_gives_the_same_page():
    assert render_review_page(CASES, VIEWS) == render_review_page(CASES, VIEWS)


def test_german_labels_cover_the_testset_vocabulary():
    data = page_data(render_review_page(CASES, VIEWS))
    assert [key for key, _ in data["outcomes"]] == list(script.OUTCOME_ORDER)
    assert set(script.OUTCOME_ORDER) == set(OUTCOMES) and len(script.OUTCOME_ORDER) == len(OUTCOMES)
    assert [key for key, _ in data["verdicts"]] == list(VERDICTS)
    assert set(data["kinds"]) == set(KINDS) and set(data["faults"]) == set(FAULTS)
    assert all(label for _, label in data["outcomes"] + data["verdicts"])
    assert all(label for label in [*data["kinds"].values(), *data["faults"].values()])


# --- safety: nothing in a case can break out of the page ------------------------------------------------------------

def test_script_breakout_is_escaped():  # Review Focus 2
    claim = 'X </script><script>alert(1)</script> <!-- <script>'
    html = render_review_page([case(claim=claim, note="</SCRIPT><img src=x onerror=alert(2)>")],
                              {"dev-001": view(page_text="</script><script>alert(3)</script>")})
    assert "</script><script>alert(1)" not in html
    assert "</script><script>alert(3)" not in html
    assert "<!--" not in html
    assert len(re.findall(r"</script", html, re.I)) == 2  # the data block and the page's own script, nothing else
    data = page_data(html)
    assert data["cases"][0]["claim"] == claim  # the escaping is undone by the JSON parser
    assert data["cases"][0]["view"]["page_text"] == "</script><script>alert(3)</script>"


def test_page_loads_nothing_external():
    html = render_review_page(CASES, VIEWS)
    assert not re.search(r"""(?:src|href|action|poster|data)\s*=\s*["']?\s*(?:https?:|//)""", html, re.I)
    assert not re.search(r"<link\b|<iframe\b|<object\b|<embed\b|\bimport\s*\(|\bfetch\s*\(|XMLHttpRequest",
                         html, re.I)
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "@import" not in style and "url(" not in style.lower()  # (the script's own URL.createObjectURL is a blob)
    assert not re.search(r"<script\b[^>]*\bsrc=", html, re.I)
    assert not re.search(r"""https?://""", code_of(html))


def test_page_never_uses_innerhtml_for_data():
    html = render_review_page(CASES, VIEWS)
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in html


def test_pdf_links_are_only_set_for_file_urls():
    assert 'startsWith("file:")' in code_of(render_review_page(CASES, VIEWS))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_page_script_is_valid_javascript(tmp_path):
    path = tmp_path / "page.js"
    path.write_text(code_of(render_review_page(CASES, VIEWS)), encoding="utf-8")
    done = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- the log in the browser: git and several tabs ----------------------------------------------------------------------

def test_default_output_is_ignored_by_git():
    """The page embeds the draft labels and the booklet text: `git add -A` must never pick it up."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    done = subprocess.run(["git", "check-ignore", "-q", str(script.DEFAULT_OUT)], cwd=config.REPO_ROOT)
    if done.returncode == 128:
        pytest.skip("not a git work tree")
    assert done.returncode == 0, f"{script.DEFAULT_OUT} is not ignored: add .cache/ to the root .gitignore"


def test_page_merges_the_stored_log_before_it_writes_and_listens_to_other_tabs():
    code = code_of(render_review_page(CASES, VIEWS))
    assert 'addEventListener("storage"' in code
    write_log = re.search(r"function writeLog\(\) \{(.*?)\n  \}", code, re.S).group(1)
    assert write_log.index("mergeStored()") < write_log.index("storageSet(KEY")  # re-read, merge, then write


# Runs the page's real script twice ("tab A" and "tab B") against one shared localStorage, with a stub DOM.
TWO_TABS = r"""
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const data = /<script type="application\/json" id="review-data">([\s\S]*?)<\/script>/.exec(html)[1];
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
const code = scripts[scripts.length - 1][1];
const KEY = JSON.parse(data).storageKey;

const shared = new Map();  // the localStorage both tabs see
const storage = {
  getItem: (key) => (shared.has(key) ? shared.get(key) : null),
  setItem: (key, value) => { shared.set(key, String(value)); },
  removeItem: (key) => { shared.delete(key); },
};
let clock = Date.parse("2026-11-01T10:00:00Z");  // every `new Date()` is one second later, in both tabs
class FakeDate extends Date {
  constructor(...args) { super(...(args.length ? args : [(clock += 1000)])); }
}

function fakeNode() {
  const node = {children: [], attrs: {}, dataset: {}, style: {}, className: "", textContent: "", value: "",
    hidden: false, disabled: false,
    append(...items) { node.children.push(...items); },
    replaceChildren(...items) { node.children = items; },
    addEventListener() {}, setAttribute(name, value) { node.attrs[name] = String(value); },
    querySelector: () => fakeNode(), remove() {}, click() {}, focus() {}, blur() {}, scrollIntoView() {},
    matches: () => false, closest: () => null,
    get firstElementChild() { return node.children.find((child) => typeof child === "object"); }};
  return node;
}

function openTab() {
  const nodes = {};
  const listeners = {document: {}, window: {}};
  const filters = ["all", "open", "differ"].map((name) => Object.assign(fakeNode(), {dataset: {filter: name}}));
  const document = {
    getElementById: (id) => nodes[id] || (nodes[id] = id === "review-data"
      ? Object.assign(fakeNode(), {textContent: data}) : fakeNode()),
    createElement: () => fakeNode(),
    querySelectorAll: (selector) => (selector === ".filter" ? filters : []),
    addEventListener: (type, fn) => { (listeners.document[type] = listeners.document[type] || []).push(fn); },
    body: fakeNode(),
  };
  const window = {localStorage: storage, location: {hash: ""}, history: {replaceState() {}}, scrollTo() {},
    addEventListener: (type, fn) => { (listeners.window[type] = listeners.window[type] || []).push(fn); }};
  vm.runInContext(code, vm.createContext({document, window, Date: FakeDate, setTimeout, clearTimeout, URL, Blob}));
  return {
    press(code) {
      (listeners.document.keydown || []).forEach((fn) => fn({code, key: code, target: fakeNode(), shiftKey: false,
        ctrlKey: false, metaKey: false, altKey: false, repeat: false, preventDefault() {}}));
    },
    storageEvent() { (listeners.window.storage || []).forEach((fn) => fn({key: KEY})); },
    progress: () => nodes["progress-text"].textContent,
  };
}

const a = openTab();
const b = openTab();            // both tabs start on dev-001 with an empty log
a.press("Digit1");              // tab A judges dev-001
b.press("ArrowRight");          // tab B has not heard of that: it moves on to dev-002 ...
b.press("Digit2");              // ... and judges it
const progressB = b.progress();
a.storageEvent();               // the browser tells tab A about the write of tab B
const progressA = a.progress();
a.press("Digit2");              // tab A judges dev-001 again, now differently
console.log(JSON.stringify({log: JSON.parse(shared.get(KEY)), progressA, progressB}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_second_tab_neither_loses_nor_duplicates_judgements(tmp_path):
    cases = [case(), case(id="dev-002", claim="Die Gebühr steigt nicht, sagt die Behörde im Jahr 2020.")]
    (tmp_path / "page.html").write_text(render_review_page(cases, {"dev-001": view(), "dev-002": view()}),
                                        encoding="utf-8")
    (tmp_path / "two_tabs.js").write_text(TWO_TABS, encoding="utf-8")
    done = subprocess.run(["node", str(tmp_path / "two_tabs.js"), str(tmp_path / "page.html")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    log = result["log"]
    assert [event["id"] for event in log].count("dev-001") == 2 and [event["id"] for event in log].count("dev-002") == 1
    assert len({(event["id"], event["at"]) for event in log}) == len(log) == 3  # nothing lost, nothing twice
    assert result["progressB"] == "2 / 2 geprüft"  # tab B took over the judgement of tab A when it wrote
    assert result["progressA"] == "2 / 2 geprüft"  # tab A learned of tab B's judgement from the storage event


# --- build_views ----------------------------------------------------------------------------------------------------

class FakeCatalog:
    def __init__(self):
        self.votes = {V_DEV.vote_id: V_DEV, V_NEW.vote_id: V_NEW}
        self.missing = {"2026-09-27_de": "2026-09-27_de.pdf"}


class FakeEngine:
    closed = False

    def source_page_text(self, pdf, page):
        return None if page == 99 else f"Rohtext {Path(pdf).name} Seite {page}"

    def close(self):
        self.closed = True


def test_build_views_reads_the_page_text_and_links_the_pdf():
    views, warnings = script.build_views([case(), case(id="dev-002", vote_id=V_NEW.vote_id, page=7)],
                                         FakeCatalog(), FakeEngine())
    pdf = (config.BOOKLETS_DIR / "2020-02-09_de.pdf").resolve()
    assert views["dev-001"] == {"vote_title": "Bezahlbare Wohnungen", "vote_date": "2020-02-09", "page": 4,
                                "page_text": "Rohtext 2020-02-09_de.pdf Seite 4", "pdf_href": pdf.as_uri() + "#page=4"}
    assert views["dev-002"]["vote_title"] == "Feuerwerk"
    assert views["dev-002"]["pdf_href"].endswith("2026-11-29_de.pdf#page=7")
    assert warnings == []


def test_build_views_gives_no_view_to_a_case_without_a_vote():
    views, warnings = script.build_views([CASES[3]], FakeCatalog(), FakeEngine())
    assert views == {"sdev-03": {}} and warnings == []


def test_build_views_warns_instead_of_failing():
    cases = [case(id="dev-001", vote_id="2026-09-27_de#1"), case(id="dev-002", vote_id="2020-02-09_de#9"),
             case(id="dev-003", page=99), case(id="dev-004", page=None)]
    views, warnings = script.build_views(cases, FakeCatalog(), FakeEngine())
    assert set(views) == {"dev-001", "dev-002", "dev-003", "dev-004"}
    assert views["dev-001"]["page_text"] == "" and views["dev-001"]["vote_title"] == "2026-09-27_de#1"
    assert [text.split(":")[0] for text in warnings] == ["dev-001", "dev-002", "dev-003", "dev-004"]
    assert "fetch_catalog_booklets.py" in warnings[0] and "unbekannte Vorlage" in warnings[1]
    assert views["dev-003"]["page_text"] == "" and views["dev-003"]["pdf_href"].endswith("#page=99")
    assert views["dev-004"]["pdf_href"] == ""


# --- the CLI --------------------------------------------------------------------------------------------------------

def line_of(each):
    return json.dumps({**asdict(each), "claim_language": "de"}, ensure_ascii=False)


@pytest.fixture
def fake_run(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(script, "make_engine", lambda: engine)
    monkeypatch.setattr(script, "load_vote_catalog", lambda sources, directory, _engine: FakeCatalog())
    return engine


def test_script_writes_the_page_and_prints_its_path(tmp_path, capsys, fake_run):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text("\n".join(line_of(each) for each in CASES) + "\n", encoding="utf-8")
    out = tmp_path / "nested" / "review.html"
    assert script.main(["--cases", str(cases_path), "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out.resolve())
    data = page_data(out.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in data["cases"]] == [each.id for each in read_cases(cases_path)]
    assert data["cases"][0]["view"]["page_text"].startswith("Rohtext 2020-02-09_de.pdf")
    assert data["cases"][3]["view"] == {}
    assert fake_run.closed


def test_script_default_output_is_under_the_cache_folder():
    assert script.DEFAULT_OUT == config.REPO_ROOT / ".cache" / "testset-review" / "label_review.html"


def test_script_prints_warnings_to_stderr_but_writes_the_page(tmp_path, capsys, fake_run):
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(line_of(case(vote_id="2026-09-27_de#1")) + "\n", encoding="utf-8")
    out = tmp_path / "review.html"
    assert script.main(["--cases", str(cases_path), "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert out.is_file() and captured.out.strip() == str(out.resolve())
    assert captured.err.startswith("Warnung: dev-001:") and "fetch_catalog_booklets.py" in captured.err


def test_script_without_cases_file(tmp_path, capsys):
    path = tmp_path / "fehlt.jsonl"
    assert script.main(["--cases", str(path), "--out", str(tmp_path / "x.html")]) == 1
    assert capsys.readouterr().out.strip() == f"Keine Fälle: {path} fehlt"
    assert not (tmp_path / "x.html").exists()


def test_script_names_the_bad_line_of_the_cases_file(tmp_path, capsys):
    path = tmp_path / "cases.jsonl"
    path.write_text(line_of(case()) + "\n{kaputt\n", encoding="utf-8")
    assert script.main(["--cases", str(path), "--out", str(tmp_path / "x.html")]) == 1
    assert "Zeile 2" in capsys.readouterr().out
