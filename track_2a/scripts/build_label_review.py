"""Build the blind label review page: one offline HTML file to judge every drafted case without seeing the draft.

Usage (from track_2a/):
  python scripts/build_label_review.py [--cases PATH] [--out PATH]

The page shows one case at a time: the claim, the vote, the quote and the booklet page it comes from. The drafted result
(`expected`, `after_answer`, `note`) stays hidden until a judgement is given, then it appears as "stimmt überein" or
"weicht ab". Judgements are appended to a log in the browser (localStorage) and exported as `label_review.jsonl`, one
line per judgement: {"id", "case_sha", "expected", "after_answer", "note", "at"}. A judgement belongs to the `case_sha`
it was given for: a changed case counts as open again. Keys: 1-3 (main cases) or 1-6 (special cases) judge, Shift+1-3
judge the row "Nach der Antwort" of an ambiguous case, arrows and Enter move, N jumps to the note.

The page needs no network and no server. All text from the cases reaches the page as JSON in a data block and is put on
the screen with `textContent` only. The engine uses the mock Apertus client: no request leaves the machine.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.agent.testset import (CASES_PATH, FETCH_COMMAND, VERDICTS, EvalCase, case_sha, load_vote_catalog,
                               read_cases)
from src.apertus_client import ApertusClient
from src.booklet_catalog import load_catalog
from src.inference import ClaimVerificationEngine

DEFAULT_OUT = config.REPO_ROOT / ".cache" / "testset-review" / "label_review.html"
STORAGE_KEY = "fact-attack-testset-review-v1"
EVENT_FIELDS = ["id", "case_sha", "expected", "after_answer", "note", "at"]  # the export format, in this order
EXIT_OK, EXIT_PROBLEMS = 0, 1

# The six outcomes in the order of the keys 1-6; the first three are the verdicts (keys 1-3 on a main case).
OUTCOME_ORDER = ("gestuetzt", "widersprochen", "nicht_belegt", "rueckfrage", "quelle_fehlt", "technischer_fehler")
OUTCOME_LABELS = {"gestuetzt": "gestützt", "widersprochen": "widersprochen", "nicht_belegt": "nicht belegt",
                  "rueckfrage": "Rückfrage", "quelle_fehlt": "Quelle fehlt", "technischer_fehler": "technischer Fehler"}
KIND_LABELS = {"mehrdeutig": "Mehrdeutige Vorlage", "falscher_sprecher": "Falscher Sprecher",
               "zusammengesetzt": "Zusammengesetzte Behauptung", "ohne_abstimmung": "Thema ohne Abstimmung",
               "api_ausfall": "Simulierter API-Ausfall", "prompt_injection": "Prompt Injection"}
FAULT_LABELS = {"timeout": "Zeitüberschreitung", "http_500": "Serverfehler (HTTP 500)"}


# --- the page -------------------------------------------------------------------------------------------------------

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f5f3ee; --card: #ffffff; --text: #1f2430; --muted: #5d6675; --line: #d9d5ca;
  --accent: #2c5f93; --accent-text: #ffffff; --accent-soft: #e3edf7;
  --ok: #1b6e40; --ok-bg: #e4f3ea; --bad: #a8271f; --bad-bg: #fbe8e5;
  --warn: #7a5200; --warn-bg: #fff3d6; --mark: #ffe58f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15181d; --card: #1d2128; --text: #e8ebf0; --muted: #a3adba; --line: #343b45;
    --accent: #8db8e8; --accent-text: #0f1a27; --accent-soft: #223347;
    --ok: #6fcf97; --ok-bg: #173323; --bad: #f5948b; --bad-bg: #3b1f1d;
    --warn: #f0c36a; --warn-bg: #3a2f10; --mark: #6a5400;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--text); line-height: 1.5;
       font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
button, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
button:disabled { cursor: default; opacity: .45; }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.app { min-height: 100vh; min-height: 100dvh; max-width: 1100px; margin: 0 auto; padding: 0 16px;
       display: flex; flex-direction: column; }
.top { padding: 12px 0 8px; }
.top-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; }
.top-left { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 14px; }
h1 { margin: 0; font-size: 1rem; font-weight: 650; letter-spacing: .01em; }
.tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.ghost { background: transparent; border: 1px solid var(--line); border-radius: 8px; padding: 6px 12px;
         font-size: .9rem; min-height: 36px; }
.ghost:hover { border-color: var(--accent); }
#progress-text { font-size: 1.15rem; font-weight: 650; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); font-size: .88rem; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin: 10px 0 0; }
.bar > span { display: block; height: 100%; width: 0; background: var(--accent); border-radius: 3px; }
.filters { display: flex; flex-wrap: wrap; gap: 6px; }
.filter { background: transparent; border: 1px solid var(--line); border-radius: 999px; padding: 5px 14px;
          min-height: 36px; font-size: .92rem; }
.filter[aria-pressed="true"] { background: var(--accent-soft); border-color: var(--accent); font-weight: 600; }
.filter .count { color: var(--muted); font-variant-numeric: tabular-nums; margin-left: 4px; }
.notice { margin: 10px 0 0; padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn);
          font-size: .92rem; }
.toast { position: fixed; left: 50%; bottom: 84px; transform: translateX(-50%); z-index: 5; margin: 0;
         max-width: min(92vw, 38rem); padding: 10px 16px; border-radius: 10px; background: var(--text);
         color: var(--bg); font-size: .92rem; box-shadow: 0 4px 18px rgba(0, 0, 0, .25); }
main { flex: 1; padding: 14px 0 20px; outline: none; }
.card { display: grid; gap: 16px 28px; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
        padding: 20px 18px; grid-template-columns: minmax(0, 1fr);
        grid-template-areas: "claim" "source" "quote" "page" "judge"; }
.card.no-source { grid-template-areas: "claim" "source" "judge"; }
.label { margin: 0 0 4px; font-size: .74rem; font-weight: 650; letter-spacing: .07em; text-transform: uppercase;
         color: var(--muted); }
.claim-block { grid-area: claim; }
.meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-bottom: 10px; }
.case-id { font: .85rem ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); }
.chip { font-size: .8rem; padding: 2px 10px; border-radius: 999px; background: var(--accent-soft);
        color: var(--text); border: 1px solid var(--line); }
.claim { margin: 0; font-size: clamp(1.3rem, 1.05rem + 1.1vw, 1.75rem); line-height: 1.38; font-weight: 550;
         overflow-wrap: anywhere; }
.stale { margin: 12px 0 0; padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn);
         font-size: .92rem; }
.source { grid-area: source; }
.source p { margin: 0; overflow-wrap: anywhere; }
.source .muted { display: block; }
.quote-box { grid-area: quote; }
.quote { margin: 0; padding: 12px 14px; border-left: 4px solid var(--accent); background: var(--accent-soft);
         border-radius: 0 8px 8px 0; overflow-wrap: anywhere; white-space: pre-line; }
.page { grid-area: page; align-self: start; min-width: 0; }
.page-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 12px;
             margin-bottom: 4px; }
.page-head .label { margin: 0; }
.page-head a { color: var(--accent); font-size: .92rem; }
.pagetext { position: relative; margin: 0; max-height: min(52vh, 20rem); overflow: auto; padding: 12px 14px;
            border: 1px solid var(--line); border-radius: 8px; font-size: .9rem; line-height: 1.55;
            white-space: pre-line; overflow-wrap: anywhere; background: var(--bg); }
mark { background: var(--mark); color: inherit; border-radius: 3px; padding: 0 .1em; }
.judge { grid-area: judge; min-width: 0; }
.choice-group + .choice-group { margin-top: 14px; }
.choice-title { margin: 0 0 6px; font-size: .98rem; font-weight: 600; }
.choice-sub { font-weight: 400; color: var(--muted); font-size: .88rem; }
.choices { display: grid; gap: 8px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.choice { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 2px 8px; min-height: 52px;
          padding: 6px 10px; background: var(--card); border: 1.5px solid var(--line); border-radius: 10px;
          text-align: center; }
.choice:hover { border-color: var(--accent); }
.choice[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
kbd { font: .75rem ui-monospace, SFMono-Regular, Menlo, monospace; padding: 0 6px; border-radius: 5px;
      border: 1px solid currentColor; opacity: .7; line-height: 1.5; }
.hint { margin: 10px 0 0; }
.hint:empty { display: none; }
.reveal { margin-top: 14px; padding: 12px 14px; border-radius: 10px; border: 1px solid; scroll-margin-bottom: 96px; }
.reveal.ok { background: var(--ok-bg); border-color: var(--ok); }
.reveal.bad { background: var(--bad-bg); border-color: var(--bad); }
.reveal-verdict { margin: 0 0 8px; font-weight: 700; font-size: 1.05rem; }
.reveal.ok .reveal-verdict { color: var(--ok); }
.reveal.bad .reveal-verdict { color: var(--bad); }
.reveal dl { margin: 0; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 3px 14px; }
.reveal dt { color: var(--muted); font-size: .9rem; }
.reveal dd { margin: 0; overflow-wrap: anywhere; }
.note-field { display: block; margin-top: 14px; scroll-margin-bottom: 96px; }
.note-field textarea { display: block; width: 100%; margin-top: 4px; min-height: 56px; padding: 8px 10px;
                       resize: vertical; background: var(--card); border: 1.5px solid var(--line); border-radius: 8px; }
.keys { margin: 12px 0 0; }
.empty { margin: 40px 0; text-align: center; color: var(--muted); font-size: 1.05rem; }
.nav { position: sticky; bottom: 0; display: flex; align-items: center; justify-content: space-between; gap: 12px;
       padding: 10px 0 calc(10px + env(safe-area-inset-bottom)); background: var(--bg);
       border-top: 1px solid var(--line); }
.nav button { min-height: 48px; padding: 8px 18px; border-radius: 10px; border: 1.5px solid var(--line);
              background: var(--card); }
.nav button:not(:disabled):hover { border-color: var(--accent); }
.nav .primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); font-weight: 600; }
#position { color: var(--muted); font-size: .92rem; text-align: center; font-variant-numeric: tabular-nums; }
@media (min-width: 900px) {
  .card { grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); padding: 26px 28px;
          grid-template-areas: "claim page" "source page" "quote page" "judge page";
          grid-template-rows: auto auto auto 1fr; }
  .card.no-source { grid-template-columns: minmax(0, 1fr); max-width: 46rem; width: 100%; margin: 0 auto;
                    grid-template-areas: "claim" "source" "judge"; grid-template-rows: none; }
  .page { position: sticky; top: 16px; }
  .pagetext { max-height: calc(100vh - 240px); max-height: calc(100dvh - 240px); min-height: 12rem; }
}
@media (max-width: 899px) { .pagetext { max-height: 14rem; } }
@media (max-width: 520px) {
  .keyhint { display: none; }
  .choices.n3 { grid-template-columns: minmax(0, 1fr); }
  .choices.n6 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .card { padding: 16px 14px; }
  .nav button { padding: 8px 12px; }
}
@media (hover: none) { .keys { display: none; } }
@media (prefers-reduced-motion: no-preference) { .choice, .ghost, .filter { transition: border-color .12s; } }
"""

_BODY = """
<div class="app">
  <header class="top">
    <div class="top-row">
      <div class="top-left">
        <h1>Testset prüfen</h1>
        <span id="progress-text">0 / 0 geprüft</span>
      </div>
      <div class="filters" role="group" aria-label="Filter">
        <button type="button" class="filter" data-filter="all" aria-pressed="true">Alle<span class="count"></span></button>
        <button type="button" class="filter" data-filter="open" aria-pressed="false">Offen<span class="count"></span></button>
        <button type="button" class="filter" data-filter="differ" aria-pressed="false">Abweichungen<span class="count"></span></button>
      </div>
      <div class="tools">
        <span class="muted" id="backup-text"></span>
        <button type="button" class="ghost" id="export" title="Alle Urteile herunterladen">Exportieren</button>
        <button type="button" class="ghost" id="import" title="Urteile aus einer Datei wiederherstellen">Importieren</button>
        <input type="file" id="import-file" accept=".jsonl,.json,.txt" hidden>
      </div>
    </div>
    <div class="bar" id="bar" role="progressbar" aria-label="Fortschritt" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><span id="bar-fill"></span></div>
    <p class="notice" id="notice" role="status" hidden>Speichern im Browser nicht möglich – bitte regelmässig exportieren</p>
  </header>
  <main id="case" tabindex="-1"><noscript><p class="empty">Diese Seite braucht JavaScript.</p></noscript></main>
  <nav class="nav" aria-label="Fälle">
    <button type="button" id="prev">Zurück <span class="keyhint">(←)</span></button>
    <span id="position"></span>
    <button type="button" id="next" class="primary">Weiter <span class="keyhint">(→ / Enter)</span></button>
  </nav>
  <p class="toast" id="message" role="status" aria-live="polite" hidden></p>
</div>
"""

_SCRIPT = r"""
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("review-data").textContent);
  const KEY = DATA.storageKey;
  const EXPORTED_KEY = KEY + "-exported";  // when the log was exported last
  const PROBE_KEY = KEY + "-probe";
  const FIELDS = DATA.eventFields;
  const EXPORT_NAME = "label_review.jsonl";
  const cases = DATA.cases;
  const position = new Map(cases.map((c, i) => [c.id, i]));
  const outcomes = DATA.outcomes;  // [key, label] in the order of the keys 1-6
  const verdicts = DATA.verdicts;  // [key, label] in the order of the keys 1-3
  const outcomeLabel = new Map(outcomes);
  const verdictLabel = new Map(verdicts);
  const EMPTY_TEXT = {
    all: "Diese Datei enthält keine Fälle.",
    open: "Nichts offen: Alle Fälle sind beurteilt.",
    differ: "Keine Abweichungen: Alle Urteile stimmen mit dem Entwurf überein."
  };
  const FILTER_LABEL = {all: "Alle", open: "Offen", differ: "Abweichungen"};

  const $ = (id) => document.getElementById(id);
  const main = $("case");
  const prevButton = $("prev");
  const nextButton = $("next");
  const positionText = $("position");
  const filterButtons = Array.from(document.querySelectorAll(".filter"));

  let events = [];            // the whole log in the order it was written
  let latest = new Map();     // case id -> its newest judgement
  let current = 0;            // index of the case on screen
  let filter = "all";
  let emptyView = false;      // the filter has no case: a message instead of a card
  let sel = {expected: null, after: null};  // the labels of the case on screen
  let ui = null;              // the elements of the case on screen
  let storageOk = true;
  let lastExport = null;      // time of the newest event the last export contained
  let messageTimer = null;

  // --- helpers ------------------------------------------------------------------------------------------------

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function formatDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    return m ? m[3] + "." + m[2] + "." + m[1] : (iso || "");
  }

  function time(event) {
    return Date.parse(event.at);
  }

  function say(text) {  // a short message above the navigation, gone after a while
    const toast = $("message");
    toast.textContent = text;
    toast.hidden = !text;
    clearTimeout(messageTimer);
    if (text) messageTimer = setTimeout(() => say(""), 9000);
  }

  // --- the log in the browser ---------------------------------------------------------------------------------

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (error) {
      storageOk = false;
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
      return true;
    } catch (error) {
      storageOk = false;
      return false;
    }
  }

  function makeEvent(values) {
    const event = {};
    FIELDS.forEach((name) => { event[name] = values[name]; });
    return event;
  }

  function normalise(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    const after = raw.after_answer === undefined ? null : raw.after_answer;
    const valid = typeof raw.id === "string" && typeof raw.case_sha === "string" && outcomeLabel.has(raw.expected)
      && typeof raw.at === "string" && !Number.isNaN(Date.parse(raw.at))
      && (after === null || verdictLabel.has(after));
    if (!valid) return null;
    return makeEvent({id: raw.id, case_sha: raw.case_sha, expected: raw.expected, after_answer: after,
                      note: typeof raw.note === "string" ? raw.note : "", at: raw.at});
  }

  function readLog() {
    const raw = storageGet(KEY);
    if (!raw) return [];
    try {
      const list = JSON.parse(raw);
      return Array.isArray(list) ? list.map(normalise).filter(Boolean) : [];
    } catch (error) {
      return [];
    }
  }

  function writeLog() {
    storageSet(KEY, JSON.stringify(events));
    refreshNotice();
  }

  function probeStorage() {
    try {
      window.localStorage.setItem(PROBE_KEY, "1");
      window.localStorage.removeItem(PROBE_KEY);
    } catch (error) {
      storageOk = false;
    }
  }

  function refreshNotice() {
    $("notice").hidden = storageOk;
  }

  function recompute() {
    latest = new Map();
    events.forEach((event) => {
      const previous = latest.get(event.id);
      if (!previous || time(event) >= time(previous)) latest.set(event.id, event);
    });
  }

  // --- what a judgement says about a case ---------------------------------------------------------------------

  function validEvent(c) {  // the newest judgement, if it was given for this very version of the case
    const event = latest.get(c.id);
    return event && event.case_sha === c.sha ? event : null;
  }

  function isStale(c) {
    const event = latest.get(c.id);
    return Boolean(event) && event.case_sha !== c.sha;
  }

  function agrees(c, event) {
    return event.expected === c.expected && (c.kind !== "mehrdeutig" || event.after_answer === c.after_answer);
  }

  function status(c) {  // "open", "agree" or "differ"
    const event = validEvent(c);
    if (!event) return "open";
    return agrees(c, event) ? "agree" : "differ";
  }

  function inFilter(c) {
    return filter === "all" || status(c) === filter;
  }

  function step(direction) {  // the next case in the filter before or after the current one, -1 if there is none
    for (let i = current + direction; i >= 0 && i < cases.length; i += direction) {
      if (inFilter(cases[i])) return i;
    }
    return -1;
  }

  // --- header and navigation ----------------------------------------------------------------------------------

  function refreshHeader() {
    const counts = {open: 0, agree: 0, differ: 0};
    cases.forEach((c) => { counts[status(c)] += 1; });
    const done = counts.agree + counts.differ;
    $("progress-text").textContent = done + " / " + cases.length + " geprüft";
    const bar = $("bar");
    bar.setAttribute("aria-valuemax", String(cases.length));
    bar.setAttribute("aria-valuenow", String(done));
    $("bar-fill").style.width = (cases.length ? Math.round(100 * done / cases.length) : 0) + "%";
    filterButtons.forEach((button) => {
      const name = button.dataset.filter;
      const n = name === "all" ? cases.length : counts[name];
      button.querySelector(".count").textContent = String(n);
    });
    const unsaved = events.filter((event) => lastExport === null || time(event) > lastExport).length;
    $("backup-text").textContent = !events.length ? "" : unsaved
      ? plural(unsaved, "Urteil", "Urteile") + " noch nicht exportiert" : "Alles exportiert";
  }

  function refreshNav() {
    prevButton.disabled = emptyView || step(-1) < 0;
    nextButton.disabled = emptyView || step(1) < 0;
    positionText.textContent = emptyView || !cases.length ? "" : "Fall " + (current + 1) + " von " + cases.length;
  }

  function go(direction) {
    if (emptyView) return;
    const target = step(direction);
    if (target < 0) {
      const where = direction > 0 ? "letzter" : "erster";
      say(filter === "all" ? "Das ist der " + where + " Fall."
        : "Kein weiterer Fall im Filter „" + FILTER_LABEL[filter] + "“ in dieser Richtung.");
      return;
    }
    showCase(target);
  }

  function setFilter(name) {
    filter = name;
    filterButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.filter === name)));
    let target = -1;
    for (let i = current; i < cases.length && target < 0; i += 1) if (inFilter(cases[i])) target = i;
    for (let i = 0; i < current && target < 0; i += 1) if (inFilter(cases[i])) target = i;
    if (target < 0) showEmpty();
    else if (target === current && !emptyView) refreshJudgement();
    else showCase(target);
  }

  function showEmpty() {
    emptyView = true;
    ui = null;
    main.replaceChildren(el("p", "empty", EMPTY_TEXT[filter]));
    refreshHeader();
    refreshNav();
  }

  // --- the case on screen -------------------------------------------------------------------------------------

  function buildClaim(c) {
    const block = el("section", "claim-block");
    const meta = el("div", "meta");
    meta.append(el("span", "case-id", c.id));
    if (c.kind) meta.append(el("span", "chip", DATA.kinds[c.kind] || c.kind));
    if (c.fault) meta.append(el("span", "chip", DATA.faults[c.fault] || c.fault));
    block.append(meta, el("h2", "label", "Behauptung"), el("p", "claim", c.claim));
    const stale = el("p", "stale", "Fall wurde geändert, bitte neu beurteilen");
    stale.hidden = true;
    block.append(stale);
    ui.stale = stale;
    return block;
  }

  function buildSource(c) {
    const section = el("section", "source");
    section.append(el("h2", "label", "Vorlage"));
    const line = el("p");
    if (!c.vote_id) {
      line.className = "muted";
      line.textContent = "Keine Vorlage: Zu diesem Fall gibt es weder Zitat noch Seitentext.";
    } else {
      const view = c.view || {};
      line.append(el("strong", null, view.vote_title || c.vote_id));
      if (view.vote_date) line.append(el("span", "muted", formatDate(view.vote_date)));
    }
    section.append(line);
    return section;
  }

  function buildQuote(c) {
    const section = el("section", "quote-box");
    const page = c.page === null || c.page === undefined ? "" : " · S. " + c.page;
    section.append(el("h2", "label", "Zitat" + page), el("blockquote", "quote", c.quote || "(kein Zitat)"));
    return section;
  }

  function shown(text) {  // a soft hyphen at the end of a line of the PDF reads as a hyphen
    return text.replace(/\u00ad\n/g, "-\n");
  }

  function buildPage(c) {
    const view = c.view || {};
    const section = el("section", "page");
    const head = el("div", "page-head");
    head.append(el("h2", "label", "Seitentext" + (c.page ? " · S. " + c.page : "")));
    if (typeof view.pdf_href === "string" && view.pdf_href.startsWith("file:")) {
      const link = el("a", null, "PDF S. " + c.page + " öffnen");
      link.href = view.pdf_href;
      link.target = "_blank";
      link.rel = "noopener";
      head.append(link);
    }
    const box = el("div", "pagetext");
    box.tabIndex = 0;
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "Seitentext des Büchleins");
    const text = view.page_text || "";
    const quote = c.quote || "";
    const at = text && quote ? text.indexOf(quote) : -1;  // marked only where the quote stands character for character
    if (at >= 0) {
      const mark = el("mark", null, shown(quote));
      box.append(shown(text.slice(0, at)), mark, shown(text.slice(at + quote.length)));
      ui.mark = mark;
    } else {
      box.textContent = text ? shown(text) : "(kein lesbarer Text)";
    }
    ui.pageBox = box;
    section.append(head, box);
    return section;
  }

  function scrollToMark() {
    if (ui && ui.mark && ui.pageBox) ui.pageBox.scrollTop = Math.max(0, ui.mark.offsetTop - 48);
  }

  function choiceRow(rowName, title, sub, options, shifted) {
    const group = el("div", "choice-group");
    const heading = el("h3", "choice-title", title);
    if (sub) heading.append(" ", el("span", "choice-sub", sub));
    heading.id = "row-" + rowName;
    group.setAttribute("role", "group");
    group.setAttribute("aria-labelledby", heading.id);
    const list = el("div", "choices n" + options.length);
    options.forEach(([key, label], n) => {
      const button = el("button", "choice");
      button.type = "button";
      button.dataset.row = rowName;
      button.dataset.value = key;
      button.setAttribute("aria-keyshortcuts", (shifted ? "Shift+" : "") + (n + 1));
      button.append(el("span", null, label), el("kbd", null, (shifted ? "⇧" : "") + (n + 1)));
      button.addEventListener("click", (event) => {
        choose(rowName, key);
        if (event.detail) button.blur();  // a mouse click: keep Enter free for "Weiter"
      });
      list.append(button);
      ui.choices.push(button);
    });
    group.append(heading, list);
    return group;
  }

  function buildJudge(c) {
    const section = el("section", "judge");
    const main3 = c.group === "haupt";
    section.append(choiceRow("expected",
      main3 ? "Was sagt das Büchlein zu dieser Behauptung?" : "Welches Ergebnis ist richtig?", "",
      main3 ? verdicts : outcomes, false));
    if (c.kind === "mehrdeutig") {
      section.append(choiceRow("after", "Nach der Antwort", "Urteil, sobald die Vorlage geklärt ist", verdicts, true));
    }
    const hint = el("p", "muted hint");
    ui.hint = hint;
    ui.revealHost = el("div");
    const label = el("label", "note-field");
    label.append(el("span", "label", "Notiz (optional, N)"));
    const note = el("textarea");
    note.rows = 2;
    note.placeholder = "z. B. Zitat passt nicht zur Behauptung";
    note.addEventListener("blur", commit);
    label.append(note);
    ui.note = note;
    section.append(hint, ui.revealHost, label);
    const keys = el("p", "muted keys");
    keys.append("Tasten: ", el("kbd", null, c.group === "haupt" ? "1 2 3" : "1–6"),
      c.kind === "mehrdeutig" ? " und " : "", c.kind === "mehrdeutig" ? el("kbd", null, "⇧ 1 2 3") : "",
      " urteilen · ", el("kbd", null, "←"), " ", el("kbd", null, "→"), " blättern · ", el("kbd", null, "Enter"),
      " weiter · ", el("kbd", null, "N"), " Notiz");
    section.append(keys);
    return section;
  }

  function buildReveal(c, event) {
    const ok = agrees(c, event);
    const box = el("div", "reveal " + (ok ? "ok" : "bad"));
    box.setAttribute("role", "status");
    box.append(el("p", "reveal-verdict", ok ? "✓ stimmt überein" : "✕ weicht ab"));
    const rows = el("dl");
    const add = (name, value) => { rows.append(el("dt", null, name), el("dd", null, value)); };
    const mixed = c.kind === "mehrdeutig";
    if (!ok) {
      add("Dein Urteil", outcomeLabel.get(event.expected) + (mixed ? " · nach der Antwort: "
        + verdictLabel.get(event.after_answer) : ""));
    }
    add("Entwurf", outcomeLabel.get(c.expected) || c.expected);
    if (mixed) add("Nach der Antwort", verdictLabel.get(c.after_answer) || String(c.after_answer));
    const others = (c.acceptable || []).filter((key) => key !== c.expected).map((key) => outcomeLabel.get(key) || key);
    if (others.length) add("Auch zulässig", others.join(", "));
    if (mixed && c.ambiguous_with.length) add("Mehrdeutig mit", c.ambiguous_with.join(", "));
    if (c.note) add("Notiz zum Entwurf", c.note);
    box.append(rows);
    return box;
  }

  function buildCard(c) {
    ui = {choices: [], mark: null, pageBox: null, stale: null, hint: null, revealHost: null, note: null};
    const card = el("article", "card" + (c.vote_id ? "" : " no-source"));
    card.append(buildClaim(c), buildSource(c));
    if (c.vote_id) card.append(buildQuote(c), buildPage(c));
    card.append(buildJudge(c));
    return card;
  }

  function showCase(index) {
    current = index;
    emptyView = false;
    const c = cases[index];
    const event = validEvent(c);
    sel = {expected: event ? event.expected : null, after: event ? event.after_answer : null};
    const card = buildCard(c);
    const newest = latest.get(c.id);
    ui.note.value = newest ? newest.note : "";
    main.replaceChildren(card);
    scrollToMark();
    window.scrollTo(0, 0);
    try {
      window.history.replaceState(null, "", "#" + encodeURIComponent(c.id));
    } catch (error) { /* a page without history access still works */ }
    refreshJudgement();
  }

  function refreshJudgement() {
    if (emptyView || !ui) {
      refreshHeader();
      refreshNav();
      return;
    }
    const c = cases[current];
    ui.choices.forEach((button) => {
      const chosen = button.dataset.row === "expected" ? sel.expected : sel.after;
      button.setAttribute("aria-pressed", String(button.dataset.value === chosen));
    });
    ui.stale.hidden = !isStale(c);
    const event = validEvent(c);
    ui.revealHost.replaceChildren();
    if (event) {
      ui.revealHost.append(buildReveal(c, event));
      ui.hint.textContent = "";
    } else {
      ui.hint.textContent = c.kind === "mehrdeutig"
        ? "Beide Zeilen beurteilen, dann erscheint der Entwurf." : "Der Entwurf erscheint nach deinem Urteil.";
    }
    refreshHeader();
    refreshNav();
  }

  // --- judging ------------------------------------------------------------------------------------------------

  function commit() {  // append a judgement once every label of the case is set
    if (emptyView || !ui) return;
    const c = cases[current];
    const mixed = c.kind === "mehrdeutig";
    if (!sel.expected || (mixed && !sel.after)) return;
    const event = makeEvent({id: c.id, case_sha: c.sha, expected: sel.expected, after_answer: mixed ? sel.after : null,
                             note: ui.note.value, at: new Date().toISOString()});
    const before = validEvent(c);
    if (before && before.expected === event.expected && before.after_answer === event.after_answer
        && before.note === event.note) return;
    events.push(event);
    recompute();
    writeLog();
    refreshJudgement();
    const reveal = ui.revealHost.firstElementChild;  // the draft may sit below the fold: bring it into view
    if (reveal) reveal.scrollIntoView({block: "nearest"});
  }

  function choose(rowName, value) {
    if (emptyView || !ui) return;
    sel[rowName] = value;
    refreshJudgement();
    commit();
  }

  function chooseByKey(n, shifted) {
    if (emptyView || !ui) return;
    const c = cases[current];
    if (shifted) {
      if (c.kind === "mehrdeutig" && n <= verdicts.length) choose("after", verdicts[n - 1][0]);
      return;
    }
    const options = c.group === "haupt" ? verdicts : outcomes;
    if (n <= options.length) choose("expected", options[n - 1][0]);
  }

  // --- export and import --------------------------------------------------------------------------------------

  function exportLog() {
    if (!events.length) {
      say("Noch keine Urteile zum Exportieren.");
      return;
    }
    const ordered = events.map((event, i) => [event, i])
      .sort((a, b) => (time(a[0]) - time(b[0])) || (a[1] - b[1])).map((pair) => pair[0]);
    const line = (event) => JSON.stringify(event).replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
    const text = ordered.map(line).join("\n") + "\n";
    const url = URL.createObjectURL(new Blob([text], {type: "application/x-ndjson"}));
    const link = el("a");
    link.href = url;
    link.download = EXPORT_NAME;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    lastExport = Math.max(...events.map(time));
    storageSet(EXPORTED_KEY, new Date(lastExport).toISOString());
    refreshHeader();
    say(plural(ordered.length, "Urteil", "Urteile") + " als " + EXPORT_NAME
      + " heruntergeladen. Bitte in track_2a/data/genai/testset/ ablegen.");
  }

  function importLog(text) {
    const seen = new Set(events.map((event) => event.id + "\n" + event.at));
    let added = 0, duplicate = 0, unknown = 0, broken = 0;
    text.split("\n").forEach((line) => {
      if (!line.trim()) return;
      let event = null;
      try {
        event = normalise(JSON.parse(line));
      } catch (error) { /* counted below */ }
      if (!event) { broken += 1; return; }
      if (!position.has(event.id)) { unknown += 1; return; }
      const key = event.id + "\n" + event.at;
      if (seen.has(key)) { duplicate += 1; return; }
      seen.add(key);
      events.push(event);
      added += 1;
    });
    recompute();
    writeLog();
    if (emptyView) setFilter(filter);
    else showCase(current);
    const parts = [plural(added, "Urteil", "Urteile") + " importiert"];
    if (duplicate) parts.push(duplicate + " schon vorhanden");
    if (unknown) parts.push(plural(unknown, "Urteil", "Urteile") + " zu unbekannten Fällen übersprungen");
    if (broken) parts.push(plural(broken, "Zeile", "Zeilen") + " nicht lesbar");
    say(parts.join(" · "));
  }

  // --- keys and buttons ---------------------------------------------------------------------------------------

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target.matches && target.matches("textarea, input, select")) {
      if (event.key === "Escape") target.blur();
      return;
    }
    const digit = /^(?:Digit|Numpad)([1-6])$/.exec(event.code || "");
    if (digit) {
      event.preventDefault();
      chooseByKey(Number(digit[1]), event.shiftKey);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      go(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      go(1);
    } else if (event.key === "Enter") {
      if (event.repeat || (target.closest && target.closest("button, a, summary"))) return;
      event.preventDefault();
      go(1);
    } else if ((event.key === "n" || event.key === "N") && ui && ui.note) {
      event.preventDefault();
      ui.note.focus();
    }
  });

  function onClick(button, handler) {
    button.addEventListener("click", (event) => {
      handler();
      if (event.detail) button.blur();
    });
  }

  onClick(prevButton, () => go(-1));
  onClick(nextButton, () => go(1));
  filterButtons.forEach((button) => onClick(button, () => setFilter(button.dataset.filter)));
  onClick($("export"), exportLog);
  const importInput = $("import-file");
  onClick($("import"), () => importInput.click());
  importInput.addEventListener("change", () => {
    const file = importInput.files && importInput.files[0];
    if (!file) return;
    file.text().then(importLog, () => say("Die Datei lässt sich nicht lesen."));
    importInput.value = "";
  });

  // --- start --------------------------------------------------------------------------------------------------

  function addressId() {  // the case id after the # of the address, "" if there is none
    try {
      return decodeURIComponent((window.location.hash || "").slice(1));
    } catch (error) {
      return "";
    }
  }

  function startIndex() {  // the case in the address, else the one after the case judged last
    const wanted = addressId();
    if (position.has(wanted)) return position.get(wanted);
    const last = events.length ? position.get(events[events.length - 1].id) : undefined;
    if (last === undefined) return 0;
    return Math.min(last + 1, cases.length - 1);
  }

  window.addEventListener("hashchange", () => {  // an address with a case id (e.g. #dev-018) shows that case
    const wanted = addressId();
    if (position.has(wanted) && position.get(wanted) !== current) showCase(position.get(wanted));
  });

  probeStorage();
  refreshNotice();
  events = readLog();
  const exportedAt = Date.parse(storageGet(EXPORTED_KEY) || "");
  lastExport = Number.isNaN(exportedAt) ? null : exportedAt;
  recompute();
  if (cases.length) showCase(startIndex());
  else setFilter("all");
})();
"""


def _json_for_script(payload: dict) -> str:
    """JSON that can sit in a <script> block: `<!--` and `</` are masked so no case text can end or hide the block."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<!--", "\\u003c!--").replace("</", "<\\/")


def render_review_page(cases: List[EvalCase], views: Dict[str, dict]) -> str:
    """The review page for the cases, as one self-contained HTML text.

    `views[case.id]` is `{"vote_title", "vote_date", "page", "page_text", "pdf_href"}`, or `{}` for a case without a
    vote (`ohne_abstimmung`). `page_text` is the raw page text; `pdf_href` is a `file://` URL ending in `#page=<n>`.
    """
    payload = {
        "storageKey": STORAGE_KEY,
        "eventFields": EVENT_FIELDS,
        "outcomes": [[key, OUTCOME_LABELS[key]] for key in OUTCOME_ORDER],
        "verdicts": [[key, OUTCOME_LABELS[key]] for key in VERDICTS],
        "kinds": KIND_LABELS,
        "faults": FAULT_LABELS,
        "cases": [{**asdict(case), "sha": case_sha(case), "view": views.get(case.id, {})} for case in cases],
    }
    return (
        '<!doctype html>\n<html lang="de">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n<title>Testset prüfen</title>\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>{_BODY}\n"
        '<script type="application/json" id="review-data">' + _json_for_script(payload) + "</script>\n"
        f"<script>{_SCRIPT}</script>\n</body>\n</html>\n")


# --- views and CLI ----------------------------------------------------------------------------------------------

def make_engine() -> ClaimVerificationEngine:
    return ClaimVerificationEngine(apertus_client=ApertusClient(mock=True))


def build_views(cases: List[EvalCase], votes, engine) -> Tuple[Dict[str, dict], List[str]]:
    """The `views` of `render_review_page` for the cases, and one warning per case whose page could not be shown.

    `votes` is a `VoteCatalog`; `engine` reads the raw page text. A case that cannot be shown completely still gets a
    view (its vote id as title, an empty page text) so the page lists it, and a warning that says why.
    """
    views: Dict[str, dict] = {}
    warnings: List[str] = []
    for case in cases:
        if case.vote_id is None:
            views[case.id] = {}
            continue
        ref = votes.votes.get(case.vote_id)
        if ref is None:
            source_id = case.vote_id.split("#", 1)[0]
            reason = (f"Büchlein fehlt ({votes.missing[source_id]}): {FETCH_COMMAND} ausführen"
                      if source_id in votes.missing else f"unbekannte Vorlage {case.vote_id}")
            warnings.append(f"{case.id}: {reason}")
            views[case.id] = {"vote_title": case.vote_id, "vote_date": "", "page": case.page, "page_text": "",
                              "pdf_href": ""}
            continue
        pdf = (config.BOOKLETS_DIR / f"{ref.source_id}.pdf").resolve()
        text = engine.source_page_text(pdf, case.page) if case.page is not None else None
        if not text:
            warnings.append(f"{case.id}: S. {case.page} von {case.vote_id} hat keinen lesbaren Text")
        views[case.id] = {"vote_title": ref.title, "vote_date": ref.date, "page": case.page, "page_text": text or "",
                          "pdf_href": pdf.as_uri() + f"#page={case.page}" if case.page is not None else ""}
    return views, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, default=CASES_PATH, metavar="PATH",
                        help=f"cases.jsonl (Standard: {CASES_PATH})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, metavar="PATH",
                        help=f"die HTML-Seite (Standard: {DEFAULT_OUT})")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.cases.is_file():
        print(f"Keine Fälle: {args.cases} fehlt")
        return EXIT_PROBLEMS
    try:
        cases = read_cases(args.cases)
    except ValueError as exc:  # a line of cases.jsonl the reader rejects
        print(exc)
        return EXIT_PROBLEMS
    engine = make_engine()
    try:
        sources = load_catalog(config.DATA_DIR / "booklet_catalog.json")
        votes = load_vote_catalog(sources, config.BOOKLETS_DIR, engine)
        views, warnings = build_views(cases, votes, engine)
    finally:
        engine.close()
    for warning in warnings:
        print(f"Warnung: {warning}", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_review_page(cases, views), encoding="utf-8")
    print(args.out.resolve())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
