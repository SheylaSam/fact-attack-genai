"""
Fact Attack 2026 - Multilingual Voting Booklet NLI & Fact-Checker
Streamlit Web Application for Hack Apertus Track 2A (OST).
Showcases document-grounded claim verification with Apertus v1.5 (CSCS): hybrid retrieval, cited booklet pages, efficiency per check.
"""

import sys
import os
import json
import time
import re
import html
import sqlite3
import hashlib
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import streamlit as st

# Setup paths
_pkg_root = Path(__file__).resolve().parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

from src.pdf_parser import PDFParser
from src.pdf_preview import render_evidence_page
from src.retriever import PassageRetriever
from src.apertus_client import ApertusClient
from src.inference import ClaimVerificationEngine, PredictionResult, EvidenceSource
from src.evaluator import BenchmarkEvaluator
from src.results_summary import build_summary
from src.text_utils import best_snippet, guess_language
from src import config
from src.check_report import render_check_report, batch_report_records
from src.batch_check import suggest_statements, run_checks, retry_failed, batch_summary, export_batch, MAX_CHECKS
from src.ui_i18n import LANGUAGES, translate, translate_static_html, date_label
from src.check_history import save_check, load_checks, matching_source, set_removed, export_checks, filter_checks, sort_checks
from src.booklet_catalog import load_catalog, available_sources, date_options, languages_for_date, filter_sources
from src.ui_strategies import STANDARD, strategy_options, strategy_from_label, rag_available, store_uploaded_source, load_completed_dev42
from src import agent_app, agent_ui, ui_theme  # without langgraph: the agent layer itself is imported only on the agent path

# Page Configuration
st.set_page_config(
    page_title="Fact Attack 2026",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="expanded",
)

UI_LANGUAGE = st.session_state.get("ui_language", "de")
# Streamlit keeps the wire label of keyed selections; resend canonical values
# when only their display language changes.
if st.session_state.get("_last_ui_language") != UI_LANGUAGE:
    for selection_key in ("nav", "source_year", "ballot", "vote", "claim_lang", "strategy_option", "history_view", "history_label", "history_origin", "history_order"):
        if selection_key in st.session_state:
            st.session_state[selection_key] = st.session_state[selection_key]
    st.session_state["_last_ui_language"] = UI_LANGUAGE


def t(text):
    return translate(text, UI_LANGUAGE)


def th(markup):
    return translate_static_html(markup, UI_LANGUAGE)


# =============================================================================
# DESIGN SYSTEM — calm light and dark look, palettes and styles in src/ui_theme
# =============================================================================
st.markdown(ui_theme.theme_css("dark" if st.session_state.get("ui_dark") else "light"), unsafe_allow_html=True)


# =============================================================================
# Cache Resources
# =============================================================================
@st.cache_resource(on_release=lambda resource: resource.close())
def get_engine():
    return ClaimVerificationEngine()

@st.cache_resource
def get_parser():
    return PDFParser()

engine = get_engine()
parser = get_parser()


# =============================================================================
# HELPER: VISUAL PDF FACSIMILE HIGHLIGHTING
# =============================================================================
def highlight_evidence_in_page(page_text: str, quote: str, label: int) -> str:
    """Highlights the exact evidence quote within the raw page text, handling arbitrary PDF linebreaks."""
    if not quote or not page_text:
        return html.escape(page_text)

    # Strip tags and quotation marks
    clean_quote = re.sub(r"^\[(?:page|seite)\s+\d+\]\s*\d*\s*", "", quote, flags=re.IGNORECASE).strip(' "«»')
    cls_name = "hl-entailment" if label == 0 else ("hl-contradiction" if label == 2 else "hl-neutral")

    # Normalize words
    words = [re.escape(w) for w in clean_quote.split() if len(w) > 0]
    if not words:
        return html.escape(page_text)

    # Search with flexible whitespace \s+ (matches spaces, tabs, newlines across tokens)
    for span_len in (len(words),):
        if span_len < 2:
            break
        pattern_str = r"\s+".join(words[:span_len])
        try:
            match = re.search(pattern_str, page_text, re.IGNORECASE)
            if match:
                start, end = match.span()
                before = html.escape(page_text[:start])
                matched = html.escape(page_text[start:end])
                after = html.escape(page_text[end:])
                return f"{before}<mark class=\"{cls_name}\">{matched}</mark>{after}"
        except re.error:
            pass

    return html.escape(page_text)


@st.cache_data(max_entries=12, show_spinner=False)
def original_page_preview(pdf_bytes, page_number, quote, label):
    return render_evidence_page(pdf_bytes, page_number, quote, label)


def show_original_page(pdf_path, source, snippet, label, page_text):
    with st.expander(t('Originalseite {v0} öffnen').format(v0=source.page_number), expanded=False):
        st.caption(t('PDF-Seite {v0} · Die gedruckte Seitenzahl kann abweichen.').format(v0=source.page_number))
        try:
            preview = original_page_preview(pdf_path.read_bytes(), source.page_number, snippet.removesuffix(" …"), label)
            if preview.highlighted:
                st.caption(t(preview.reason))
            else:
                st.info(t(preview.reason))
            st.image(preview.png, caption=f"Original-PDF · Seite {source.page_number}", width="stretch")
        except Exception:
            st.warning(t("Die Originalseite lässt sich gerade nicht darstellen. Der Lesetext und das Original-PDF bleiben verfügbar."))
        with st.expander(t("Lesetext der Seite"), expanded=False):
            st.markdown(highlight_evidence_in_page(page_text, snippet.removesuffix(" …"), label), unsafe_allow_html=True)
        st.download_button(t("Original-PDF herunterladen"), data=pdf_path.read_bytes(),
                           file_name=pdf_path.name, mime="application/pdf",
                           key=f"original_pdf_{source.page_number}_{source.quote[:30]}")


# =============================================================================
# HELPER: SHAREABLE SOCIAL MEDIA FACT-CARD (SVG)
# =============================================================================
def generate_fact_card_svg(
    claim: str,
    label: int,
    quote: str,
    page_number: Optional[int],
    proposal_id: Optional[int],
    date_str: str,
) -> str:
    """Generates a high-resolution, vector SVG Fact-Card for social media & sharing."""
    if label == 0:
        verdict_color = "#10b981"
        verdict_text = "GESTÜTZT"
        verdict_icon = "✓"
        bg_glow = "rgba(16, 185, 129, 0.15)"
    elif label == 1:
        verdict_color = "#f59e0b"
        verdict_text = "NICHT BELEGT"
        verdict_icon = "?"
        bg_glow = "rgba(245, 158, 11, 0.15)"
    else:
        verdict_color = "#ef4444"
        verdict_text = "WIDERSPROCHEN"
        verdict_icon = "✗"
        bg_glow = "rgba(239, 68, 68, 0.15)"

    clean_claim = (claim[:120] + "...") if len(claim) > 120 else claim
    clean_quote = (quote[:140] + "...") if len(quote) > 140 else quote
    page_str = f"Seite {page_number}" if page_number else "Amtliche Quelle"
    prop_str = f" · Vorlage {proposal_id}" if proposal_id else ""

    e_claim = html.escape(clean_claim)
    e_quote = html.escape(clean_quote)

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="760" height="420" viewBox="0 0 760 420" style="max-width: 100%; height: auto;">
  <defs>
    <linearGradient id="cardGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#090d16"/>
    </linearGradient>
  </defs>

  <!-- Outer Card Frame -->
  <rect width="760" height="420" rx="20" fill="url(#cardGrad)" stroke="#334155" stroke-width="2"/>

  <!-- Top Swiss Banner -->
  <path d="M 0 20 Q 0 0 20 0 L 740 0 Q 760 0 760 20 L 760 52 L 0 52 Z" fill="#d52b1e"/>

  <!-- Swiss Cross Icon -->
  <rect x="24" y="15" width="22" height="22" rx="4" fill="white"/>
  <rect x="27" y="23" width="16" height="6" fill="#d52b1e"/>
  <rect x="32" y="18" width="6" height="16" fill="#d52b1e"/>

  <!-- Banner Text -->
  <text x="56" y="31" fill="#ffffff" font-family="'Inter', sans-serif" font-size="13" font-weight="700" letter-spacing="1">FAKTENCHECK GEGEN DAS ABSTIMMUNGSBÜCHLEIN · FACT ATTACK</text>
  <text x="736" y="31" fill="#ffffff" text-anchor="end" font-family="'Inter', sans-serif" font-size="11" opacity="0.9">KEIN AMTLICHES DOKUMENT</text>

  <!-- Verdict Stamp -->
  <rect x="36" y="74" width="340" height="48" rx="10" fill="{bg_glow}" stroke="{verdict_color}" stroke-width="2"/>
  <text x="52" y="104" fill="{verdict_color}" font-family="'Inter', sans-serif" font-size="19" font-weight="800" letter-spacing="1">{verdict_icon} {verdict_text}</text>

  <!-- Reference Meta -->
  <rect x="396" y="74" width="328" height="48" rx="10" fill="#1e293b" stroke="#475569" stroke-width="1"/>
  <text x="412" y="93" fill="#94a3b8" font-family="'Inter', sans-serif" font-size="10" font-weight="600" text-transform="uppercase">REFERENZ-DOKUMENT</text>
  <text x="412" y="110" fill="#f8fafc" font-family="'Inter', sans-serif" font-size="12" font-weight="600">{page_str}{prop_str} · {date_str}</text>

  <!-- Claim Section -->
  <text x="36" y="152" fill="#94a3b8" font-family="'Inter', sans-serif" font-size="11" font-weight="700" text-transform="uppercase" letter-spacing="0.5">GEPRÜFTE BEHAUPTUNG:</text>
  <rect x="36" y="164" width="688" height="64" rx="10" fill="#141c2e" stroke="#253248" stroke-width="1"/>
  <foreignObject x="48" y="172" width="664" height="48">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: 'Inter', sans-serif; font-size: 13px; color: #f1f5f9; line-height: 1.4; font-weight: 500;">
      "{e_claim}"
    </div>
  </foreignObject>

  <!-- Evidence Section -->
  <text x="36" y="254" fill="#94a3b8" font-family="'Inter', sans-serif" font-size="11" font-weight="700" text-transform="uppercase" letter-spacing="0.5">BELEG AUS DEM ABSTIMMUNGSBÜCHLEIN ({page_str}):</text>
  <rect x="36" y="266" width="688" height="82" rx="10" fill="#181e2b" stroke="{verdict_color}" stroke-opacity="0.4" stroke-width="1.5"/>
  <rect x="36" y="266" width="5" height="82" rx="2" fill="{verdict_color}"/>
  <foreignObject x="52" y="274" width="658" height="66">
    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: 'Inter', sans-serif; font-size: 12.5px; color: #e2e8f0; font-style: italic; line-height: 1.45;">
      "{e_quote}"
    </div>
  </foreignObject>

  <!-- Footer -->
  <line x1="36" y1="372" x2="724" y2="372" stroke="#334155" stroke-width="1"/>
  <text x="36" y="395" fill="#64748b" font-family="'Inter', sans-serif" font-size="11">Geprüft mit <b>Apertus v1.5</b> (CSCS) · Kein politischer Rat</text>
  <text x="724" y="395" fill="#64748b" text-anchor="end" font-family="'Inter', sans-serif" font-size="11">Quelle: Abstimmungsbüchlein, bk.admin.ch</text>
</svg>"""
    return svg


# =============================================================================
# DEMO DATA & HELPERS FOR THE FACT-CHECK TAB
# =============================================================================
SOURCE_METADATA = load_catalog(config.DATA_DIR / "booklet_catalog.json")
AVAILABLE_SOURCES = available_sources(config.BOOKLETS_DIR, SOURCE_METADATA)
BOOKLET_CATALOG = date_options(AVAILABLE_SOURCES)
VOTE_AUTO = "Automatisch erkennen"
HISTORY_PATH = config.DATA_DIR / ".local-history/checks.sqlite3"
PAGES = ("Faktencheck", "Prüfverlauf", "Ergebnisse & Vergleiche", "So funktioniert’s")
RECENT_CHECKS = 5  # entries under «Zuletzt geprüft» in the sidebar

# Wording describes the relationship to the booklet, never a verdict on truth (OST ethics guideline)
VERDICTS = {
    0: ("supported", "fa-check-circle", "Vom Büchlein gestützt",
        "Das Büchlein enthält Aussagen, die diese Behauptung stützen."),
    1: ("not-proven", "fa-minus-circle", "Im Büchlein nicht belegt",
        "Im geprüften Kontext wurde keine Aussage gefunden, die diese Behauptung bestätigt oder widerlegt."),
    2: ("contradicted", "fa-times-circle", "Widerspricht dem Büchlein",
        "Das Büchlein enthält Aussagen, die dieser Behauptung widersprechen."),
}
VERDICT_TONES = {0: "green", 1: "grey", 2: "red"}

SECTION_DE = {
    "Arguments of the initiative/referendum committee": "Argumente des Komitees",
    "Arguments of the Federal Council and Parliament": "Argumente Bundesrat & Parlament",
    "Voting text (legal text)": "Abstimmungstext",
    "Official explanation in detail": "Erläuterungen im Detail",
    "Overview / summary": "Übersicht",
}


@st.cache_data
def load_demo_data():
    cases = json.loads((config.DATA_DIR / "demo_cases.json").read_text(encoding="utf-8"))["cases"]
    votes = json.loads((config.DATA_DIR / "demo_votes.json").read_text(encoding="utf-8"))
    return cases, votes


DEMO_CASES, DEMO_VOTES = load_demo_data()


@st.cache_resource
def warm_up_demo_booklets():
    """Parse the demo booklets once at startup so the first live check is not slowed by PDF parsing."""
    for case in DEMO_CASES:
        pdf = config.BOOKLETS_DIR / f"{case['booklet_date']}_{case['booklet_language']}.pdf"
        if pdf.exists():
            engine._get_booklet_data(pdf)
    return True


warm_up_demo_booklets()


def _report_source(record):
    return {**record, "source_url": SOURCE_METADATA.get(record.get("source_file"), {}).get("url")}


def _reset_history_filters():
    st.session_state["history_query"] = ""
    st.session_state["history_label"] = "Alle Ergebnisse"
    st.session_state["history_start"] = None
    st.session_state["history_end"] = None
    st.session_state["history_origin"] = "Alle Antwortarten"
    st.session_state["history_order"] = "Neueste zuerst"


def _open_saved(check_id: str) -> None:
    """Button callback of «Zuletzt geprüft»: opens the saved check on the history page, whatever filter was set."""
    st.session_state["nav"] = "Prüfverlauf"
    _reset_history_filters()
    st.session_state["history_view"] = "Aktive Prüfungen"
    st.session_state["history_selected"] = check_id


def _short_claim(text: str) -> str:
    return textwrap.shorten(text, width=42, placeholder=" …")


def _upload_key() -> str:
    """Key of the PDF uploader. Popping the key does not empty an uploader that still shows its file: the browser
    sends the file again with the next rerun. Forgetting an upload therefore moves on to a new key, a new and empty
    uploader."""
    return f"uploaded_pdf_{st.session_state.get('upload_round', 0)}"


def _remove_upload() -> None:
    """Forgets the own PDF: the uploader (by a new key) and the copy kept for page switches. Button callback of
    «Eigenes PDF entfernen»; choosing an example calls it too."""
    st.session_state.pop("kept_upload", None)
    st.session_state.pop(_upload_key(), None)
    st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1


def _upload_changed() -> None:
    """The user emptied the uploader with its own control: the kept upload goes too. (A page switch empties the
    uploader without this callback, so the kept upload stays.)"""
    if st.session_state.get(_upload_key()) is None:
        st.session_state.pop("kept_upload", None)


def _reset_source_filters():
    st.session_state["source_query"] = ""
    st.session_state["source_year"] = "Alle Jahre"


def _apply_case(case: dict, set_active: bool = True) -> None:
    """Button callback: load a demo case into the widgets (runs before the widgets are rebuilt)."""
    ballot = next(name for name, date in BOOKLET_CATALOG.items() if date == case["booklet_date"])
    st.session_state["ballot"] = ballot
    st.session_state["booklet_lang"] = case["booklet_language"].upper()
    st.session_state["vote"] = case["vote"]
    st.session_state["claim_input"] = case["claim"]
    st.session_state["claim_lang"] = case["claim_language"].upper()
    st.session_state["active_case"] = case["id"] if set_active else None
    _remove_upload()  # an example's source is its official booklet, never an upload
    st.session_state["agent_own_source"] = True  # and in agent mode that booklet is the given source
    _reset_source_filters()


def find_cached_case(claim: str, booklet_file: str, vote: Optional[str]) -> Optional[dict]:
    for case in DEMO_CASES:
        if (case["claim"].strip() == claim.strip()
                and f"{case['booklet_date']}_{case['booklet_language']}.pdf" == booklet_file
                and case["vote"] == vote):
            return case
    return None


# ── Agent check in the fact-check tab ────────────────────────────────────────
AGENT_KEYS = ("agent_session", "agent_turn", "agent_signature", "agent_events")
AGENT_BAD_UPLOAD = "Dieses PDF kann nicht gelesen werden. Lade eine andere Datei hoch oder entferne sie."
AGENT_UNKNOWN_SOURCE = ("Der Agent kann die gewählte Quelle nicht prüfen. "
                        "Wähle eine andere Quelle oder lass den Agenten die Vorlage suchen.")
AGENT_MOCK_NOTICE = "Testmodus, kein Apertus-Urteil"
CHECK_HINT = ("Behauptung und Textstellen gehen an Apertus (CSCS). Kein allgemeiner Wahrheitscheck, "
              "keine Abstimmungsempfehlung.")  # the one hint line below the button in agent mode
AGENT_PLACEHOLDER = ("Hier erscheint dein Ergebnis mit der passenden Belegstelle. Tippe eine Behauptung ein und "
                     "starte die Prüfung; die Vorlage sucht der Agent selbst.")  # the empty result: no source to pick
AGENT_BATCH_NEEDS_SOURCE = ("Für die Mehrfachprüfung die Quelle unter «Quelle selbst wählen» vorgeben "
                            "oder ein eigenes PDF laden.")


def _drop_agent_check() -> None:
    """Forgets the tab's agent check; a worker it still runs gets no further request (its budget is cancelled)."""
    check = st.session_state.get("agent_session")
    if check is not None:
        check.cancel()
    for key in AGENT_KEYS:
        st.session_state.pop(key, None)


def _vote_title(title: str) -> str:
    """A vote title as shown: the app's placeholders for a whole booklet and an own PDF are translated, an official
    title stays in its original language."""
    return t(title) if title in (agent_ui.TITLE_WHOLE_BOOKLET, agent_ui.TITLE_UPLOAD) else title


def _md(text) -> str:
    """Run-derived text (exception messages, model output) for st.error/st.warning, which render Markdown: every
    Markdown and LaTeX character is escaped, so the text is shown as written."""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>~$])", r"\\\1", str(text))


def _last_vote():
    """The session memory: the vote the person chose last (an answered question of this browser session), if the
    agent's index still holds it. It only becomes a hint for the agent and an extra button, never a choice."""
    vote_id = st.session_state.get("last_vote_id")
    return agent_app.agent_votes().get(vote_id) if vote_id else None


def _vote_date(iso_date: str) -> str:
    return date_label(next(iter(date_options({"vote": {"date": iso_date}}))), UI_LANGUAGE)


def _new_agent_check(own_source: bool, pdf_path: Path, vote: Optional[str], upload_language: Optional[str] = None):
    """A new AgentCheck for the tab's selection, or None after a shown message. With `own_source` the chosen
    booklet (and vote) is the fixed vote; a booklet chosen without a vote, or a French or Italian booklet the
    German-only index does not know, is checked as a whole when the index has no whole-booklet vote for it (the
    session registers that vote itself); a selection that is none of these is a translated message. With `upload_language` the stored upload at `pdf_path` is the fixed vote, also
    registered by the session; an unreadable file is a translated message, never the library's text."""
    try:
        with st.spinner(t("Der Agent wird vorbereitet …")):
            fixed_vote_id, session_vote = None, None
            if upload_language is not None:
                from src.agent.uploads import upload_vote  # the agent layer is imported on the agent path only
                try:
                    session_vote = upload_vote(pdf_path, upload_language)
                except ValueError:
                    st.error(t(AGENT_BAD_UPLOAD))
                    return None
            elif own_source:
                votes = agent_app.agent_votes()
                try:
                    chosen = agent_ui.vote_for_selection(votes, pdf_path.stem, vote, booklets_dir=config.BOOKLETS_DIR,
                                                         catalog=AVAILABLE_SOURCES)
                except ValueError:
                    st.error(t(AGENT_UNKNOWN_SOURCE))
                    return None
                if chosen.vote_id in votes:
                    fixed_vote_id = chosen.vote_id
                else:  # the whole booklet, unknown to the shared index
                    session_vote = chosen
            if session_vote is not None:
                return agent_app.AgentCheck(
                    lambda fixed, on_event: agent_app.make_agent_session(fixed, on_event, session_vote=session_vote),
                    session_vote.vote_id)
            return agent_app.AgentCheck(agent_app.make_agent_session, fixed_vote_id)
    except Exception as exc:  # no index, a client that cannot start: the check cannot start
        st.error(t('Die Prüfung konnte nicht abgeschlossen werden ({v0}).').format(v0=_md(exc)))
        return None


def _step_icon(row) -> tuple:
    """(Font Awesome class, tone) of a step: by its kind; rows of the old titles by their mark."""
    if row.kind in agent_ui.STEP_ICONS:
        return agent_ui.STEP_ICONS[row.kind]
    if row.mark in agent_ui.STEP_ICONS:  # error, running
        return agent_ui.STEP_ICONS[row.mark]
    return ("fa-question", "yellow") if row.mark == "ask" else ("fa-circle", "grey")


def _step_card(row) -> str:
    """One step as a card: its icon in the tone of its kind, the sentence, the agent's own text folded away, an error
    in full. Everything from the trace or the model is escaped; the markup has no blank line (it stays one block)."""
    icon, tone = _step_icon(row)
    alert = ' role="alert"' if row.mark == "error" else ""
    body = (f'<div class="step-error-label">{html.escape(t("Schritt fehlgeschlagen"))}</div>' if row.mark == "error" else "")
    body += f'<div class="step-title">{html.escape(agent_ui.row_text(row, t, _vote_date))}</div>'
    if row.detail:
        body += (f'<details class="step-details"><summary>{html.escape(t("Details"))}</summary>'
                 f'<div>{html.escape(row.detail)}</div></details>')
    if row.error:
        body += f'<div class="step-text step-error">{html.escape(row.error)}</div>'
    return (f'<div class="agent-step agent-step-{row.mark} step-card" data-kind="{html.escape(row.kind)}"{alert}>'
            f'<i class="fas {html.escape(icon)} tone-{html.escape(tone)}" aria-hidden="true"></i>'
            f'<div class="step-body">{body}</div></div>')


def _check_statement(statement: str) -> None:
    """A clicked statement of a compound claim (before the rerun): it becomes the claim in the input and is checked
    like a typed claim on «Behauptung prüfen», as a new check with new tools and budget, never as an answer."""
    st.session_state["claim_input"] = statement
    st.session_state["agent_check_statement"] = True


def _draw_agent_steps(placeholder, check, events, *, question=None, statements=()) -> Optional[str]:
    """Draws the finished step list into `placeholder`, an error row red. With an open `question` (a `Pending`)
    its text and one button per option sit inside the last ask step, plus the "wie zuletzt" button of the question's
    `last_vote`. Returns the vote id of a clicked button. With `statements` (the model asked which statement of a
    compound claim to check) one button per statement sits there instead; a click starts a new check with it."""
    rows = agent_ui.step_rows(events)
    asks = [index for index, row in enumerate(rows) if row.mark == "ask"]
    question_at = asks[-1] if asks else len(rows) - 1
    chosen = None
    with placeholder.container():
        st.markdown("#### " + t("So prüft der Agent"))
        for index, row in enumerate(rows):
            st.markdown(_step_card(row), unsafe_allow_html=True)
            if question is not None and index == question_at:
                st.markdown(t(agent_ui.question_text(events)))  # the card above carries the title
                for option in agent_ui.options_with_last(question.options, question.last_vote):
                    label = t(agent_ui.TEXT_LAST_VOTE).format(v0=option.label) if option.is_last else option.label
                    if st.button(f"{label} · {_vote_date(option.vote.date)}", width="stretch",
                                 key=f"agent_option_{check.key}_{option.vote.vote_id}"):
                        chosen = option.vote.vote_id
            if statements and index == question_at:
                st.markdown(t(agent_ui.TEXT_STATEMENT))
                for number, statement in enumerate(statements):  # the model's text: shown, never rendered
                    st.button(_md(statement), width="stretch", key=f"agent_statement_{check.key}_{number}",
                              on_click=_check_statement, args=(statement,))
    return chosen


def _draw_live_steps(placeholder, events) -> None:
    """The step list while the check runs, as one element: a container redrawn with fewer children keeps the
    surplus ones of its previous drawing, a single element is replaced whole."""
    lines = "".join(_step_card(row) for row in agent_ui.step_rows(events, running=True))
    placeholder.markdown(f'<div class="agent-steps-live"><h4>{html.escape(t("So prüft der Agent"))}</h4>{lines}</div>',
                         unsafe_allow_html=True)


def _run_agent(placeholder, check, call) -> None:
    """Runs `call(session)` through the check and redraws the step list with every event."""
    events = list(check.session.events)
    _draw_live_steps(placeholder, events)
    for event in check.run(call):
        events.append(event)
        _draw_live_steps(placeholder, events)


def _adopt_agent_turn(check, save_history: bool) -> None:
    """Keeps the turn the check's worker left, with the check's events. A verdict becomes `last_check` (the result
    card) and, if wanted, a history entry; questions and states are never saved."""
    done = check.take()
    if done is None:
        return
    kind, turn = done
    st.session_state["agent_events"] = list(check.session.events)
    st.session_state["agent_turn"] = turn  # an exception of start/answer is shown as an error
    if kind == "turn" and getattr(turn, "outcome", None) == "quelle_fehlt":
        if agent_ui.asks_to_pick_source(turn):  # tick «Diese Quelle vorgeben» with the expander (before its widget)
            st.session_state["agent_pick_source"] = True
        st.rerun()  # «Weitere Möglichkeiten» was drawn before this turn existed; it opens for "pick the source" now
    if kind != "turn" or agent_app.is_question(turn) or turn.prediction is None:
        return
    if agent_ui.outcome_view(turn).kind != "verdict":
        return
    vote = turn.vote
    source = (config.BOOKLETS_DIR / f"{vote.source_id}.pdf", vote.date or None, vote.language, vote.title)  # an upload: no date
    prediction = agent_ui.prediction_for_card(turn)
    mock = agent_app.mock_mode()  # the mock client's verdict is no Apertus verdict: marked, never saved
    st.session_state["last_check"] = {"signature": st.session_state["agent_signature"], "result": prediction,
                                      "notice": AGENT_MOCK_NOTICE if mock else None,
                                      "origin": "mock" if mock else "live",
                                      "created_at": datetime.now(timezone.utc).isoformat(),
                                      "source": source, "discarded": agent_app.is_discarded(turn)}
    if save_history and not mock:
        try:
            save_check(HISTORY_PATH, result=prediction.model_dump(mode="json"), source_path=source[0],
                       date=source[1], language=vote.language, vote=vote.title)
        except (OSError, ValueError, sqlite3.Error):
            st.warning(t("Das Ergebnis ist verfügbar, konnte aber nicht im lokalen Verlauf gespeichert werden."))


def _show_agent_outcome(turn) -> None:
    """A state (no source, technical error) or a failed start/answer; verdicts are shown by the result card."""
    if turn is None:
        return
    if isinstance(turn, Exception):
        st.error(t('Die Prüfung konnte nicht abgeschlossen werden ({v0}).').format(v0=_md(turn)))
        return
    if agent_app.is_question(turn) or agent_ui.statement_choices(turn):  # the question sits in its step
        return
    view = agent_ui.outcome_view(turn)
    if view.kind != "verdict":
        show = {"ok": st.success, "warn": st.warning, "error": st.error, "info": st.info}[view.tone]
        text = t(view.text) if view.text in agent_ui.TEXT_KEYS else _md(view.text)  # else the run's own error
        show(f"**{t(view.title)}** · {text}")


def run_agent_check(steps, verify_clicked: bool, claim: str, claim_lang: str, signature: tuple,
                    own_source: bool, pdf_path: Path, vote: Optional[str], save_history: bool,
                    upload_language: Optional[str] = None) -> None:
    """The fact-check tab on the agent: starts a check on «Behauptung prüfen», shows its steps live in `steps`, the
    question inside its step and continues the same check with the clicked option. Draws into the current column.
    With `upload_language` the PDF at `pdf_path` is an own upload in that language and the fixed source. A click
    while a check with the same input still runs (a double click) adopts that check instead of starting another."""
    current = st.session_state.get("agent_session")
    adopt = current is not None and current.running and st.session_state.get("agent_signature") == signature
    if verify_clicked and claim.strip() and not adopt:
        st.session_state.pop("batch_check", None)
        st.session_state.pop("last_check", None)
        _drop_agent_check()
        check = _new_agent_check(own_source, pdf_path, vote, upload_language)
        if check is not None:
            st.session_state.update(agent_session=check, agent_signature=signature, agent_turn=None, agent_events=[])
            last_vote = _last_vote()
            _run_agent(steps, check, lambda session: session.start(claim, claim_lang, last_vote=last_vote))
    check = st.session_state.get("agent_session")
    if check is None:
        return
    if check.running:  # a worker of a run that a second click stopped: wait for it, never ask the session twice
        _draw_live_steps(steps, check.session.events)
        with st.spinner(t("Die Prüfung läuft noch …")):
            check.wait()
    _adopt_agent_turn(check, save_history)
    turn = st.session_state.get("agent_turn")
    chosen = _draw_agent_steps(steps, check, st.session_state["agent_events"],
                               question=turn if agent_app.is_question(turn) else None,
                               statements=agent_ui.statement_choices(turn))
    if chosen is not None and check.session.pending is not None and not check.running:
        st.session_state["last_vote_id"] = chosen  # the memory of this browser session only, never saved
        _run_agent(steps, check, lambda session: session.answer(chosen))
        _adopt_agent_turn(check, save_history)
        turn = st.session_state.get("agent_turn")
        _draw_agent_steps(steps, check, st.session_state["agent_events"],
                          statements=agent_ui.statement_choices(turn))
    _show_agent_outcome(turn)




# =============================================================================
# UI LAYOUT
# =============================================================================

# ── Sidebar: recent checks (after the pages, see recent_slot) ───────────────────────────────────────────────
def _draw_recent_checks() -> None:
    try:
        recent_checks = load_checks(HISTORY_PATH)[:RECENT_CHECKS]  # active entries, newest first
    except (OSError, ValueError, sqlite3.Error):
        recent_checks = []  # the history page says that the history cannot be read
    if recent_checks:
        with st.expander(t("Zuletzt geprüft"), expanded=False):
            for record in recent_checks:
                label = record["result"].get("label")
                known = label in VERDICTS and label in agent_ui.OUTCOME_WORDS  # an odd stored label: no icon, no word
                short = _md(_short_claim(record["result"]["claim"]))
                with st.container(horizontal=True, vertical_alignment="center", gap="small", wrap=False):
                    if known:
                        st.markdown(f'<i class="fas {VERDICTS[label][1]} tone-{VERDICT_TONES[label]} recent-icon" aria-hidden="true"></i>',
                                    unsafe_allow_html=True, width="content")
                    st.button(f"{short} · {t(agent_ui.OUTCOME_WORDS[label])}" if known else short,
                              key=f"recent_{record['id']}", on_click=_open_saved, args=(record["id"],),
                              type="tertiary", width="stretch", wrap=True)  # the verdict word stays readable

# ── Sidebar: brand, navigation, recent checks, language, dark mode ──────────
with st.sidebar:
    st.markdown('<div class="sidebar-brand"><div class="logo-mark">+</div><span class="brand-text">Fact Attack</span></div>',
                unsafe_allow_html=True)
    page = st.radio(t("Navigation"), PAGES, key="nav", format_func=t, label_visibility="collapsed")
    recent_slot = st.sidebar.container()  # filled after the page ran, so a check saved in this run is listed
    st.selectbox("Sprache / Langue / Lingua", list(LANGUAGES), format_func=LANGUAGES.get, key="ui_language",
                 help=t("Die Sprache der Oberfläche ändert weder die Behauptung noch die Quelle. Forschungsnotizen, Beispieltitel und Exporte behalten ihre Originalsprache."))
    st.toggle(t("Dunkler Modus"), key="ui_dark")

# ── Hero Section ─────────────────────────────────────────────────────────────
st.markdown(th("""
<div class="hero-section">
    <h1>Was steht wirklich im Büchlein?</h1>
    <p>Prüfe eine politische Behauptung am offiziellen Abstimmungsbüchlein. Mit Belegstelle zum Nachlesen.</p>
</div>
"""), unsafe_allow_html=True)


# ── Pages (chosen in the sidebar) ────────────────────────────────────────────
# Only the chosen page is drawn. Its keyed inputs carry persist_state="session", so they keep their values while
# another page is shown, and an open agent question (plain session state) waits for the user's return. ("session"
# rather than "page": this is one script, and AppTest builds a new PagesManager per run, which drops "page" values.)


# =============================================================================
# SEITE 1: INTERAKTIVER FAKTENCHECK
# =============================================================================
if page == PAGES[0]:
    if not BOOKLET_CATALOG:
        st.warning(t("Noch keine offiziellen Büchlein installiert. Bitte im Projektordner `python -m src.booklet_catalog` ausführen."))
        with recent_slot:
            _draw_recent_checks()
        st.stop()
    if "ballot" not in st.session_state:
        st.session_state["ballot"] = next((name for name, date in BOOKLET_CATALOG.items()
                                                 if date == "2026-06-14"), next(iter(BOOKLET_CATALOG)))
        st.session_state["active_case"] = None
        st.session_state["claim_input"] = ""
        st.session_state["claim_lang"] = "Automatisch"
        st.session_state["booklet_lang"] = "DE"
        st.session_state["vote"] = VOTE_AUTO

    if st.session_state.get("ballot") not in BOOKLET_CATALOG:
        st.session_state["ballot"] = next(iter(BOOKLET_CATALOG))
    agent_on = agent_app.agent_available()
    uploaded_file = st.session_state.get(_upload_key())
    # A page switch empties the uploader (it cannot persist its value). The stored copy of the last own PDF stays the
    # source until the user removes it, so a result or open question on it survives the switch.
    kept_upload = st.session_state.get("kept_upload")  # (file name, stored path) of the own PDF
    if uploaded_file is None and kept_upload is not None and not Path(kept_upload[1]).is_file():
        st.session_state.pop("kept_upload", None)
        kept_upload = None
    has_upload = uploaded_file is not None or kept_upload is not None
    agent_mode = agent_on
    # «Quelle selbst wählen» after a claim in another language ticks the box too (set before its widget exists)
    picked_source = bool(st.session_state.pop("agent_pick_source", False)) and agent_mode
    if picked_source:
        st.session_state["agent_own_source"] = True
    # a clicked statement of a compound claim is in the claim input now: it starts a new check like the button
    statement_start = bool(st.session_state.pop("agent_check_statement", False)) and agent_mode
    own_upload = agent_mode and has_upload  # an own PDF is always the check's fixed source
    own_source = own_upload or (agent_mode and bool(st.session_state.get("agent_own_source")))
    if agent_mode:  # one column: claim, button, hint; everything optional under «Weitere Möglichkeiten»; then the result
        col_in = st.container()
        claim_box, verify_box = col_in.container(), col_in.container()
        more_box = col_in.expander(t("Weitere Möglichkeiten"), expanded=own_source or picked_source or getattr(
            st.session_state.get("agent_turn"), "outcome", None) == "quelle_fehlt")
        source_tab, example_tab, search_tab, batch_tab, lang_tab = more_box.tabs(
            [t(agent_ui.TITLE_PICK_SOURCE), t("Beispiel auswählen"), t("Eigenes PDF & Suche"), t("Mehrere Aussagen"),
             t("Sprache der Behauptung")])
        # no expander inside an expander: each area is a container in its tab, a longer old heading a caption
        source_box, lang_box = source_tab.container(), lang_tab.container()
        example_box, search_box, batch_box = example_tab.container(), search_tab.container(), batch_tab.container()
        example_box.caption(t("Keine eigene Behauptung? Beispiel auswählen"))
        batch_box.caption(t("Mehrere Aussagen aus einem Text prüfen"))
        example_note, save_box, retry_box = claim_box, more_box.container(), batch_box
        col_out = st.container()
    else:  # without the agent: two columns, the source is required
        col_in, col_out = st.columns([1, 1.12], gap="large")
        claim_box = col_in.container()
        claim_box.markdown(th('<div class="flow-heading"><span>1</span> Was möchtest du prüfen?</div>'), unsafe_allow_html=True)
        example_box = col_in.expander(t("Keine eigene Behauptung? Beispiel auswählen"), expanded=False)
        example_note = col_in.container()
        col_in.markdown(th('<div class="flow-heading"><span>2</span> Quelle wählen</div>'), unsafe_allow_html=True)
        source_box = col_in.container()
        lang_box = col_in.expander(t("Sprache der Behauptung"), expanded=False)
        save_box, verify_box = col_in.container(), col_in.container()
        search_box = col_in.expander(t("Weitere Optionen: Suche & eigenes PDF"), expanded=False)
        batch_box = col_in.expander(t("Mehrere Aussagen aus einem Text prüfen"), expanded=False)
        retry_box = col_in.container()

    # ── Input (drawn into the boxes above, so the code order stays the order the values are needed in) ──────
    with claim_box:
        claim = st.text_area(
            t("Behauptung"), key="claim_input", persist_state="session", height=130,
            placeholder=t("Zum Beispiel: Die Vorlage erhöht das Rentenalter auf 67 Jahre."),
            label_visibility="collapsed",
        )
    with example_box:
        st.caption(t("Ein Beispiel füllt Behauptung und Quelle aus. Die Prüfung startest du anschliessend selbst."))
        for case in DEMO_CASES:
            if f"{case['booklet_date']}_{case['booklet_language']}.pdf" not in AVAILABLE_SOURCES:
                continue
            st.button(case["title"], key=f"story_{case['id']}", width="stretch",
                      on_click=_apply_case, args=(case,))
    active_case = next((c for c in DEMO_CASES if c["id"] == st.session_state.get("active_case")
                        and c["claim"] == claim), None)
    if active_case:
        example_note.caption(active_case["story"])

    with source_box:
        if own_upload:
            st.caption(t("Das eigene PDF ist die Quelle der Prüfung; der Agent sucht nur darin."))
        elif agent_mode:
            st.checkbox(t("Diese Quelle für die Prüfung vorgeben"), key="agent_own_source", persist_state="session",
                        help=t("Ohne Häkchen sucht der Agent die passende Vorlage selbst."))
        c_search, c_year = st.columns([2, 1])
        with c_search:
            source_query = st.text_input(t("Thema oder Vorlage suchen"), key="source_query", persist_state="session",
                                        placeholder=t("z. B. Mietrecht, Rente, biodiversité"),
                                        help=t("Sucht lokal in den Vorlagentiteln auf DE, FR und IT; keine Volltextsuche im PDF."))
        with c_year:
            years = ["Alle Jahre"] + sorted({entry["date"][:4] for entry in AVAILABLE_SOURCES.values()}, reverse=True)
            if st.session_state.get("source_year", "Alle Jahre") not in years:
                st.session_state["source_year"] = "Alle Jahre"
            source_year = st.selectbox(t("Jahr"), years, key="source_year", persist_state="session", format_func=t)
        filtered_sources = filter_sources(AVAILABLE_SOURCES, source_query, source_year)
        filtered_dates = date_options(filtered_sources)
        no_source_matches = not filtered_dates
        if no_source_matches:
            st.info(t("Keine passende Quelle gefunden. Versuche einen anderen Begriff oder setze die Filter zurück. Die bisherige Quelle bleibt ausgewählt."))
            filtered_dates = {st.session_state["ballot"]: BOOKLET_CATALOG[st.session_state["ballot"]]}
        elif st.session_state.get("ballot") not in filtered_dates:
            st.session_state["ballot"] = next(iter(filtered_dates))
        if source_query.strip() or source_year != "Alle Jahre":
            st.caption(t('{v0} von {v1} Terminen gefunden · Suche in Vorlagentiteln').format(v0=len(date_options(filtered_sources)), v1=len(BOOKLET_CATALOG)))
            st.button(t("Quellenfilter zurücksetzen"), on_click=_reset_source_filters)
        c_ballot, c_lang = st.columns([1.5, 1])
        with c_ballot:
            selected_ballot_name = st.selectbox(t("Abstimmung"), list(filtered_dates), key="ballot", persist_state="session", disabled=no_source_matches or has_upload, format_func=lambda label: date_label(label, UI_LANGUAGE))
            selected_date = BOOKLET_CATALOG[selected_ballot_name]
        with c_lang:
            language_options = ["DE", "FR", "IT"] if has_upload else languages_for_date(AVAILABLE_SOURCES, selected_date)
            if st.session_state.get("booklet_lang") not in language_options:
                st.session_state["booklet_lang"] = language_options[0]
            selected_lang = st.radio(t("Sprache des Büchleins"), language_options, horizontal=True, key="booklet_lang", persist_state="session")
            lang_code = selected_lang.lower()

        source_date = None if has_upload else selected_date
        pdf_path = config.BOOKLETS_DIR / f"{selected_date}_{lang_code}.pdf"
        if has_upload:
            if uploaded_file is not None:
                upload_name = uploaded_file.name
                pdf_path = store_uploaded_source(config.BOOKLETS_DIR, bytes(uploaded_file.getbuffer()))
                st.session_state["kept_upload"] = (upload_name, str(pdf_path))
            else:
                upload_name, pdf_path = kept_upload[0], Path(kept_upload[1])
            st.success(t('{v0} geladen').format(v0=upload_name))
            st.info(t("Aktive Quelle: eigenes PDF. Das Abstimmungsdatum ist unbekannt; die offizielle Datumsauswahl ist deaktiviert."))
            st.button(t("Eigenes PDF entfernen"), key="remove_upload", on_click=_remove_upload)

        vote_options = [VOTE_AUTO] + ([] if has_upload else DEMO_VOTES.get(pdf_path.name, SOURCE_METADATA.get(pdf_path.name, {}).get("votes", [])))
        if st.session_state.get("vote") not in vote_options:
            st.session_state["vote"] = VOTE_AUTO
        selected_vote = st.selectbox(t("Vorlage"), vote_options, key="vote", persist_state="session", format_func=lambda label: t(label) if label == VOTE_AUTO else label)
        vote = None if selected_vote == VOTE_AUTO else selected_vote
        if not has_upload:
            st.caption(t('{v0} Abstimmungstermine · {v1} Sprachfassungen lokal verfügbar').format(v0=len(BOOKLET_CATALOG), v1=len(AVAILABLE_SOURCES)))
            st.link_button(t("Original bei der Bundeskanzlei öffnen ↗"), SOURCE_METADATA[pdf_path.name]["url"])

    with lang_box:
        claim_lang_choice = st.selectbox(t("Erkennung"), ["Automatisch", "DE", "FR", "IT"], key="claim_lang", persist_state="session", format_func=t)
        st.caption(t("Deutsch, Französisch und Italienisch werden automatisch erkannt. Die Quelle darf eine andere Sprache haben."))
    claim_lang = guess_language(claim) if claim_lang_choice == "Automatisch" else claim_lang_choice.lower()
    with save_box:
        save_history = st.checkbox(t("Prüfung lokal im Verlauf speichern"), value=True, key="save_history", persist_state="session",
                                   help=t("Speichert Behauptung, Quelle und Ergebnis auf diesem Rechner. Der Verlauf ist für Nutzer dieser lokalen App-Installation sichtbar."))
    with verify_box:
        verify_clicked = st.button(t("Behauptung prüfen"), type="primary", width="stretch", disabled=not claim.strip() or (
            not agent_mode and no_source_matches and not has_upload)) or statement_start
        if agent_mode:  # one hint line instead of the two notes of the old flow
            st.markdown(f'<div class="disclaimer"><i class="fas fa-lock tone-grey" aria-hidden="true"></i> {t(CHECK_HINT)}</div>',
                        unsafe_allow_html=True)
        else:
            st.caption(t("Beim Prüfen werden die Behauptung und ausgewählte Textstellen an Apertus auf CSCS gesendet."))
            st.caption(t("Die Agentenschicht ist nicht verfügbar; die Prüfung läuft wie bisher ohne Agenten."))
            st.markdown(
                th('<div class="disclaimer">Vergleicht die Aussage mit dem Büchlein. Keine allgemeine Wahrheitsprüfung und keine Abstimmungsempfehlung.</div>'),
                unsafe_allow_html=True,
            )

    with search_box:
        experiments = st.checkbox(t("Vektor-Experimente aktivieren"), key="enable_vector_rag", persist_state="session")
        options = strategy_options(experiments)
        if st.session_state.get("strategy_option") not in options:
            st.session_state["strategy_option"] = STANDARD
        st.radio(t("Kontext für Apertus"), options, key="strategy_option", persist_state="session", format_func=t)
        strat = strategy_from_label(st.session_state["strategy_option"])
        if strat in ("vector", "vector_hybrid"):
            st.caption(t("Experimentelle Suche. Im bisherigen Dev-Vergleich war die Standardstrategie besser."))
            if not rag_available():
                st.info(t("Die lokale Vektorsuche ist in dieser Installation noch nicht verfügbar."))
        st.file_uploader(t("Eigenes PDF laden"), type=["pdf"], key=_upload_key(), on_change=_upload_changed,
            help=t("Die Datei wird lokal gespeichert; beim Prüfen werden ausgewählte Textstellen an Apertus gesendet."))

    with batch_box:
        batch_text = st.text_area(t("Text mit mehreren Aussagen"), key="batch_text", persist_state="session", height=140, max_chars=20000)
        st.caption(t("Lokale Satzvorschläge, keine automatische Faktenauswahl. Bitte Text und Auswahl prüfen; Abkürzungen können falsch getrennt werden."))
        candidates = suggest_statements(batch_text)
        if len(candidates) > 30:
            st.info(t("Bitte kürze den Text auf höchstens 30 Satzvorschläge."))
            candidates = []
        if st.session_state.get("batch_selected"):
            st.session_state["batch_selected"] = [value for value in st.session_state["batch_selected"] if value in candidates]
        selected_claims = st.multiselect(t("Aussagen auswählen"), candidates, key="batch_selected", persist_state="session", max_selections=MAX_CHECKS)
        st.caption(t("{n} Einzelprüfungen ausgewählt (maximal 5). Jede kann mehrere API-Anfragen auslösen. Alle verwenden die oben gewählte Quelle und Vorlage.").format(n=len(selected_claims)))
        batch_needs_source = agent_mode and not own_source  # the agent's own search does not run here
        if batch_needs_source:
            st.caption(t(AGENT_BATCH_NEEDS_SOURCE))
        batch_clicked = st.button(t("Ausgewählte Aussagen prüfen"), key="run_batch",
                                  disabled=not selected_claims or batch_needs_source
                                  or (no_source_matches and not has_upload))
    batch_signature = (tuple(selected_claims), str(pdf_path),
                       hashlib.sha256(pdf_path.read_bytes()).hexdigest() if pdf_path.exists() else None,
                       vote, strat, claim_lang_choice)

    previous_batch = st.session_state.get("batch_check")
    retry_count = sum(bool(item["error"]) for item in previous_batch["outcomes"]) if previous_batch and previous_batch["signature"] == batch_signature else 0
    retry_clicked = False
    if retry_count:
        with retry_box:
            st.caption(t("Nur {n} fehlgeschlagene Aussagen werden erneut geprüft; erfolgreiche Antworten bleiben erhalten.").format(n=retry_count))
            retry_clicked = st.button(t("Nur fehlgeschlagene Aussagen erneut prüfen"), key="retry_batch",
                                      disabled=batch_needs_source)

    if batch_clicked or retry_clicked:
        _drop_agent_check()  # an earlier agent check's steps must not stay above the multi-check's results
        with col_out:
            if not pdf_path.exists():
                st.error(t("Die gewählte PDF-Datei ist nicht verfügbar."))
            elif strat in ("vector", "vector_hybrid") and not rag_available():
                st.error(t("Die lokale Vektorsuche ist in dieser Installation noch nicht verfügbar."))
            elif engine.client.mock:
                st.error(t("Keine Live-Verbindung konfiguriert. Die Mehrfachprüfung wurde nicht gestartet."))
            else:
                def verify_one(statement):
                    language = guess_language(statement) if claim_lang_choice == "Automatisch" else claim_lang_choice.lower()
                    return engine.verify_claim(claim=statement, booklet_pdf=pdf_path,
                                               claim_language=language, strategy=strat, vote=vote)
                def save_one(prediction):
                    save_check(HISTORY_PATH, result=prediction.model_dump(mode="json"), source_path=pdf_path,
                               date=source_date, language=lang_code, vote=vote)
                progress = st.progress(0, text=t("Mehrfachprüfung wird gestartet…"))
                def update_progress(done, total, statement):
                    progress.progress(done / total, text=t("{done} von {total} Einzelprüfungen abgeschlossen · {claim}").format(
                        done=done, total=total, claim=statement[:80]))
                with st.spinner(t("Die ausgewählten Aussagen werden einzeln geprüft…")):
                    if retry_clicked:
                        outcomes = retry_failed(previous_batch["outcomes"], verify_one,
                                                save_one if save_history else None, update_progress)
                    else:
                        outcomes = run_checks(selected_claims, verify_one, save_one if save_history else None, update_progress)
                context = {"source_file": pdf_path.name, "source_sha256": batch_signature[2],
                           "date": source_date, "language": lang_code, "vote": vote, "strategy": strat,
                           "created_at": datetime.now(timezone.utc).isoformat()}
                st.session_state["batch_check"] = {"signature": batch_signature, "outcomes": outcomes, "context": context}
                st.rerun()

    # ── Verification ─────────────────────────────────────────────────────────
    if agent_mode:  # the agent finds the vote itself unless the source is given: the selection counts only then
        signature = ("agent", claim.strip(), (pdf_path.name, vote, lang_code) if own_source else None, claim_lang)
        if picked_source and st.session_state.get("agent_session") is not None:
            st.session_state["agent_signature"] = signature  # the app ticked the box: the request to pick stays shown
        if st.session_state.get("agent_signature") != signature:  # changed input: an open question is dropped
            _drop_agent_check()
        with col_out:
            run_agent_check(st.empty(), verify_clicked, claim, claim_lang, signature, own_source, pdf_path, vote,
                            save_history, upload_language=lang_code if own_upload else None)
    else:
        signature = (claim.strip(), str(pdf_path), batch_signature[2], vote, strat, claim_lang)
    if verify_clicked and claim.strip() and not agent_mode:
        st.session_state.pop("batch_check", None)
        st.session_state.pop("last_check", None)
        if not pdf_path.exists():
            with col_out:
                st.error(t('PDF nicht gefunden: `{v0}`. Bitte `python -m src download` ausführen.').format(v0=pdf_path.name))
        else:
            result, notice = None, None
            cached_case = find_cached_case(claim, pdf_path.name, vote)
            try:
                if strat in ("vector", "vector_hybrid") and not rag_available():
                    raise ModuleNotFoundError("Optionale Abhängigkeiten für die lokale Vektorsuche fehlen")
                if engine.client.mock:
                    raise RuntimeError("Kein LLM_API_KEY gesetzt")
                with col_out:
                    with st.spinner(t("Apertus prüft die Behauptung auf CSCS…")):
                        live = engine.verify_claim(
                            claim=claim, booklet_pdf=pdf_path, claim_language=claim_lang, strategy=strat, vote=vote,
                        )
                if live.error:
                    raise RuntimeError(live.error)
                result = live
            except Exception as exc:
                if cached_case and strat == "hybrid" and SOURCE_METADATA.get(pdf_path.name, {}).get("sha256") == batch_signature[2]:
                    result = PredictionResult(**cached_case["cached_result"])
                    notice = ("Die Live-Verbindung zu Apertus ist gerade nicht verfügbar. Angezeigt wird das gespeicherte "
                              "Ergebnis dieses Beispiels (vorab live auf CSCS geprüft).")
                else:
                    with col_out:
                        st.error(t('Die Prüfung konnte nicht abgeschlossen werden ({v0}).').format(v0=exc))
            if result is not None:
                st.session_state["last_check"] = {"signature": signature, "result": result, "notice": notice,
                                                       "created_at": datetime.now(timezone.utc).isoformat()}
                if save_history:
                    try:
                        save_check(HISTORY_PATH, result=result.model_dump(mode="json"), source_path=pdf_path,
                                   date=source_date, language=lang_code, vote=vote, notice=notice)
                    except (OSError, ValueError, sqlite3.Error):
                        with col_out:
                            st.warning(t("Das Ergebnis ist verfügbar, konnte aber nicht im lokalen Verlauf gespeichert werden."))

    last_check = st.session_state.get("last_check")
    result = last_check["result"] if last_check and last_check["signature"] == signature else None

    # ── Result (right column without the agent, below the input with it) ────
    with col_out:
        batch_check = st.session_state.get("batch_check")
        current_batch = batch_check if batch_check and batch_check["signature"] == batch_signature else None
        if current_batch:
            st.markdown("### " + t("Ergebnisse der Mehrfachprüfung"))
            totals = batch_summary(current_batch["outcomes"])
            metrics = st.columns(4)
            for column, key, label in zip(metrics, ("total", "successful", "failed", "save_failed"),
                                           ("Aussagen", "Erfolgreich geprüft", "Fehlgeschlagen", "Speicherfehler")):
                column.metric(t(label), totals[key])
            st.caption(t("Erfolgreich bedeutet technisch abgeschlossen, unabhängig davon, ob das Büchlein die Aussage stützt, offenlässt oder ihr widerspricht."))
            with st.expander(t("Mehrfachergebnisse exportieren"), expanded=False):
                context = current_batch.get("context", {"source_file": pdf_path.name, "source_sha256": batch_signature[2],
                           "date": source_date, "language": lang_code, "vote": vote, "strategy": strat})
                st.download_button(t("Mehrfachprüfung als JSON"), data=export_batch(current_batch["outcomes"], context),
                                   file_name="mehrfachpruefung.json", mime="application/json", key="batch_json")
                st.download_button(t("Mehrfachprüfung als CSV"), data=export_batch(current_batch["outcomes"], context, 'csv'),
                                   file_name="mehrfachpruefung.csv", mime="text/csv", key="batch_csv")
                st.download_button(t("Mehrfachprüfung als Druckbericht (HTML)"),
                    data=render_check_report([_report_source(record) for record in batch_report_records(current_batch["outcomes"], context)], UI_LANGUAGE),
                    file_name="mehrfachpruefung.html", mime="text/html", key="batch_report")
                st.caption(t("Der Export enthält auch fehlgeschlagene Aussagen und Versuchszähler. Die Original-PDF-Datei ist nicht enthalten."))
            for index, outcome in enumerate(current_batch["outcomes"]):
                with st.expander(f"{index + 1}. {outcome['claim'][:100]}", expanded=True):
                    st.text(outcome["claim"])
                    st.caption(t("Prüfversuche: {n}").format(n=outcome.get("attempts", 1)))
                    if outcome["error"]:
                        st.error(t("Diese Aussage konnte nicht geprüft werden.") + " " + outcome["error"])
                        continue
                    prediction = outcome["result"]
                    st.markdown("#### " + t(VERDICTS[prediction.label][2]))
                    if outcome["save_error"]:
                        st.warning(t("Das Ergebnis ist verfügbar, konnte aber nicht im lokalen Verlauf gespeichert werden."))
                    if prediction.label == 1:
                        st.info(t("Im geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden."))
                    for source in prediction.evidence_sources if prediction.label != 1 else []:
                        st.caption(t("Beleg {v0} · PDF-Seite {v1}").format(v0=index + 1, v1=source.page_number))
                        st.text(best_snippet(source.quote, outcome["claim"]))
            st.caption(t("Erfolgreiche Prüfungen werden einzeln im Verlauf gespeichert, wenn die lokale Speicherung aktiviert ist. Ein erneuter Start prüft alle ausgewählten Aussagen nochmals."))
        elif batch_check:
            st.info(t("Quelle oder Auswahl geändert. Die bisherigen Mehrfachergebnisse passen nicht mehr zur aktuellen Auswahl."))
        if result is None and not current_batch and not (agent_mode and st.session_state.get("agent_session")):
            stale = bool(last_check and claim.strip())
            st.markdown(th(f"""
            <div class="result-placeholder">
                <div class="claim-stage-label">{'Eingabe geändert · erneut prüfen' if stale else 'Dein Ergebnis'}</div>
                <p class="verdict-reasoning">{'«Behauptung prüfen» klicken, um das Ergebnis zu aktualisieren.' if stale else
                AGENT_PLACEHOLDER if agent_mode else
                'Hier erscheint dein Ergebnis mit der passenden Belegstelle. Gib eine Behauptung ein, wähle die Quelle und starte die Prüfung.'}</p>
                <div class="outcome-row"><i class="fas fa-check-circle tone-green" aria-hidden="true"></i><strong>{t(agent_ui.OUTCOME_WORDS[0])}</strong> {t("— passende Aussage gefunden")}</div>
                <div class="outcome-row"><i class="fas fa-minus-circle tone-grey" aria-hidden="true"></i><strong>{t(agent_ui.OUTCOME_WORDS[1])}</strong> {t("— im geprüften Kontext offen")}</div>
                <div class="outcome-row"><i class="fas fa-times-circle tone-red" aria-hidden="true"></i><strong>{t(agent_ui.OUTCOME_WORDS[2])}</strong> {t("— das Büchlein sagt etwas anderes")}</div>
                <div class="disclaimer">Bei gestützten oder widersprochenen Aussagen kannst du die Belegstelle direkt nachlesen.</div>
            </div>
            """), unsafe_allow_html=True)
        elif result is not None and not current_batch:
            if result.strategy in ("vector", "vector_hybrid"):
                st.caption("Experimentelles Ergebnis · " + ("Vektorsuche" if result.strategy == "vector" else "BM25 + Vektor"))
            if last_check.get("notice"):
                st.markdown(f'<div class="notice-box"><i class="fas fa-info-circle tone-blue" aria-hidden="true"></i> {t(last_check["notice"])}</div>', unsafe_allow_html=True)
            # an agent result names its own booklet and vote; the old flow's is the selection
            card_pdf, card_date, card_lang, card_vote = last_check.get("source") or (pdf_path, source_date, lang_code, vote)
            discarded = last_check.get("discarded", False)  # its probabilities and number check belong to a dropped label
            if "source" in last_check:
                st.caption(t("Geprüfte Vorlage: {title}").format(title=_vote_title(card_vote)))

            b_data = engine._get_booklet_data(card_pdf)
            total_pages = len(b_data["pages"])
            pages_read = len(result.context_pages) if result.context_pages else total_pages
            v_cls, v_icon, v_title, v_desc = VERDICTS[result.label]
            v_title, v_desc = t(v_title), t(v_desc)

            def fmt(n: int) -> str:
                return f"{n:,}".replace(",", "'")

            pages_by_no = {p["page_number"]: p for p in b_data["pages"]}
            section_by_page = {p["page_number"]: p.get("section") for p in b_data["paragraphs"]}
            evidence = result.evidence_sources if result.label != 1 else []
            snippets = [best_snippet(src.quote, claim) for src in evidence]
            card_extra = ""
            if evidence:
                first = evidence[0]
                pills = ""
                if first.page_number:
                    pills += f'<span class="pill"><i class="fas fa-file-alt" aria-hidden="true"></i> {t("Seite {n}").format(n=first.page_number)}</span>'
                section = SECTION_DE.get(section_by_page.get(first.page_number))
                if section:
                    pills += f'<span class="pill">{t(section)}</span>'
                if len(evidence) > 1:
                    pills += f'<span class="pill">{t("{n} weitere Belege").format(n=len(evidence) - 1)}</span>'
                card_extra = f'<div class="quote">«{html.escape(" ".join(snippets[0].split()))}»</div><div class="pill-row">{pills}</div>'
            st.markdown(f"""
            <div class="verdict-card verdict-card-{v_cls} animate-in">
                <div class="verdict-card-head">
                    <i class="fas {v_icon} tone-{VERDICT_TONES[result.label]}" aria-hidden="true"></i>
                    <div class="verdict-card-kicker">{t("Abgleich mit dem offiziellen Büchlein")}</div>
                </div>
                <h3 class="verdict-card-title">{v_title}</h3>
                <p class="verdict-card-text">{v_desc}</p>
                {card_extra}
            </div>
            """, unsafe_allow_html=True)

            report_record = {"result": result.model_dump(mode="json"), "source_file": card_pdf.name,
                             "source_sha256": hashlib.sha256(card_pdf.read_bytes()).hexdigest(),
                             "date": card_date, "language": card_lang, "vote": card_vote,
                             "created_at": last_check.get("created_at"),
                             "origin": last_check.get("origin")  # an agent check's: "live" or "mock"
                             or ("saved_demo" if last_check.get("notice") else "live")}
            st.download_button(t("Prüfbericht herunterladen (HTML)"),
                               data=render_check_report([_report_source(report_record)], UI_LANGUAGE),
                               file_name="faktencheck.html", mime="text/html", key="single_report")
            st.caption(t("Druckfähige Datei ohne externe Inhalte. Im Browser öffnen und bei Bedarf als PDF drucken."))

            if getattr(result, "numerical_conflict", None) and not discarded:
                st.markdown(f"""
                <div class="guardrail-banner guardrail-vac animate-in">
                    <i class="fas fa-calculator tone-orange guardrail-icon" aria-hidden="true"></i>
                    <div class="guardrail-text"><strong>Hinweis Zahlenabgleich:</strong> {html.escape(result.numerical_conflict)}</div>
                </div>
                """, unsafe_allow_html=True)

            primary_quote, primary_page = "", None

            def evidence_html(src, snippet: str) -> str:
                tags = f'<span class="evidence-tag tag-page">Seite {src.page_number}</span>' if src.page_number else ""
                section = SECTION_DE.get(section_by_page.get(src.page_number))
                if section:
                    tags += f'<span class="evidence-tag tag-section">{section}</span>'
                return (f'<div class="evidence-block animate-in"><div class="evidence-header">{tags}</div>'
                        f'<p class="evidence-quote">«{html.escape(snippet)}»</p></div>')

            if result.label != 1 and result.evidence_sources:
                first = result.evidence_sources[0]
                primary_quote, primary_page = snippets[0], first.page_number
                if first.page_number in pages_by_no:
                    show_original_page(card_pdf, first, snippets[0], result.label,
                                       pages_by_no[first.page_number]["text"])
                if len(result.evidence_sources) > 1:
                    with st.expander(t('Weitere Belegstellen ({v0})').format(v0=len(result.evidence_sources) - 1), expanded=False):
                        for src, snippet in zip(result.evidence_sources[1:], snippets[1:]):
                            st.markdown(evidence_html(src, snippet), unsafe_allow_html=True)
                            if src.page_number in pages_by_no and src.page_number != first.page_number:
                                show_original_page(card_pdf, src, snippet, result.label,
                                                   pages_by_no[src.page_number]["text"])
            elif result.label == 1:
                chips = "".join(
                    f'<span class="page-chip">S. {pg}{" · " + SECTION_DE[section_by_page[pg]] if SECTION_DE.get(section_by_page.get(pg)) else ""}</span>'
                    for pg in sorted(result.context_pages)
                )
                st.markdown(f"""
                <div class="evidence-block animate-in">
                    <p class="verdict-reasoning">{t("Im geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden.")}
                    {t("Apertus hat diese Seiten geprüft:")}</p>
                    <div class="page-chips">{chips}</div>
                </div>
                """, unsafe_allow_html=True)

            with st.expander(t("Details zur Prüfung"), expanded=False):
                st.caption(t('Behauptung: {v0} · Quelle: {v1} · {v2} von {v3} Quellseiten im Kontext').format(v0=claim_lang.upper(), v1=card_lang.upper(), v2=pages_read, v3=total_pages))
                if not discarded:
                    st.markdown(f"""
                    <div style="margin-bottom: 1.2rem;">
                        <div class="conf-row">
                            <span class="conf-label">Entailment</span>
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="background: var(--green); width: {result.p_entail*100:.1f}%;"></div></div>
                            <span class="conf-value">{result.p_entail*100:.0f}%</span>
                        </div>
                        <div class="conf-row">
                            <span class="conf-label">Neutral</span>
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="background: var(--grey); width: {result.p_neutral*100:.1f}%;"></div></div>
                            <span class="conf-value">{result.p_neutral*100:.0f}%</span>
                        </div>
                        <div class="conf-row">
                            <span class="conf-label">Contradiction</span>
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="background: var(--red); width: {result.p_contra*100:.1f}%;"></div></div>
                            <span class="conf-value">{result.p_contra*100:.0f}%</span>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.caption(t("Konfidenzen schätzt Apertus selbst; sie sind nicht kalibriert."))
                t1, t2, t3 = st.columns(3)
                t1.metric(t("Inferenzzeit"), f"{result.latency_ms:.0f} ms")
                t2.metric(t("Input-Tokens"), fmt(result.tokens_prompt))
                t3.metric(t("Output-Tokens"), fmt(result.tokens_completion))
                st.markdown('<div class="claim-stage-label">Ausgabe im offiziellen OST-Format</div>', unsafe_allow_html=True)
                st.json(result.to_official_dict(), expanded=False)
                if result.retrieval_query_metadata.get("embedding"):
                    st.caption(t("Reproduzierbarkeit der experimentellen Suche"))
                    st.json(result.retrieval_query_metadata["embedding"], expanded=False)

            with st.expander(t("Faktencheck-Karte zum Teilen"), expanded=False):
                card_quote = primary_quote or "Im geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden."
                card_svg = generate_fact_card_svg(
                    claim=claim, label=result.label, quote=card_quote,
                    page_number=primary_page, proposal_id=None, date_str=card_date or t("Eigenes PDF"),
                )
                st.markdown(f'<div class="fact-card-wrap">{card_svg}</div>', unsafe_allow_html=True)
                st.download_button(
                    label=t("SVG herunterladen"), data=card_svg,
                    file_name=f"faktencheck_{result.label_name.lower()}.svg", mime="image/svg+xml", width="stretch",
                )


if page == PAGES[1]:
    st.markdown(t("### Dein Prüfverlauf"))
    st.caption(t("Lokal auf diesem Rechner gespeichert · Öffnen löst keine neue Prüfung aus. Sichtbar für Nutzer dieser App-Installation."))
    history_view = st.radio(t("Ansicht"), ["Aktive Prüfungen", "Papierkorb"], horizontal=True, key="history_view", persist_state="session", format_func=t)
    in_trash = history_view == "Papierkorb"
    if st.session_state.get("history_message"):
        st.success(t(st.session_state.pop("history_message")))
    try:
        history = load_checks(HISTORY_PATH, removed=in_trash)
    except (OSError, ValueError, sqlite3.Error):
        history = []
        st.warning(t("Der lokale Verlauf lässt sich gerade nicht lesen."))
    all_history = history
    with st.expander(t("Verlauf durchsuchen"), expanded=True):
        history_query = st.text_input(t("Text in Prüfungen suchen"), key="history_query", persist_state="session",
                                     help=t("Sucht lokal in Behauptungen, Vorlagentiteln und gespeicherten Belegzitaten."))
        if st.session_state.get("history_label") not in ("Alle Ergebnisse", *agent_ui.OUTCOME_WORDS.values()):
            st.session_state["history_label"] = "Alle Ergebnisse"  # a value from before the terms were unified
        history_label = st.selectbox(t("Ergebnis filtern"),
                                    ["Alle Ergebnisse", *agent_ui.OUTCOME_WORDS.values()],
                                    key="history_label", persist_state="session", format_func=t)
        history_origin = st.selectbox(t("Antwortart"), ["Alle Antwortarten", "Liveantworten", "Demoantworten"],
                                      key="history_origin", persist_state="session", format_func=t)
        history_order = st.selectbox(t("Sortierung"), ["Neueste zuerst", "Älteste zuerst", "Behauptung A–Z"],
                                     key="history_order", persist_state="session", format_func=t)
        c_start, c_end = st.columns(2)
        with c_start:
            history_start = st.date_input(t("Prüfdatum ab (UTC)"), value=None, key="history_start", persist_state="session")
        with c_end:
            history_end = st.date_input(t("Prüfdatum bis (UTC)"), value=None, key="history_end", persist_state="session")
        if history_start and history_end and history_start > history_end:
            st.warning(t("Das Startdatum liegt nach dem Enddatum. Bitte korrigiere den Zeitraum."))
        labels = {"Alle Ergebnisse": None, **{word: label for label, word in agent_ui.OUTCOME_WORDS.items()}}
        history = filter_checks(all_history, query=history_query, label=labels[history_label],
                                start=history_start, end=history_end,
                                origin={"Alle Antwortarten": None, "Liveantworten": "live", "Demoantworten": "saved_demo"}[history_origin])
        history = sort_checks(history, {"Neueste zuerst": "newest", "Älteste zuerst": "oldest", "Behauptung A–Z": "claim"}[history_order])
        st.caption(t("{n} von {total} Prüfungen in dieser Ansicht gefunden.").format(n=len(history), total=len(all_history)))
        if history_query.strip() or history_label != "Alle Ergebnisse" or history_start or history_end or history_origin != "Alle Antwortarten" or history_order != "Neueste zuerst":
            st.button(t("Verlaufsfilter zurücksetzen"), on_click=_reset_history_filters, key="reset_history_filters")
    if not history and all_history:
        st.info(t("Keine Prüfung passt zu diesen Filtern. Die gespeicherten Einträge bleiben unverändert."))
    elif not history:
        st.info(t("Der Papierkorb ist leer." if in_trash else "Noch keine Prüfungen gespeichert. Aktiviere beim Faktencheck die lokale Speicherung."))
    else:
        indexed = {record["id"]: record for record in history}
        if st.session_state.get("history_selected") not in indexed:
            st.session_state["history_selected"] = next(iter(indexed))
        selected_id = st.selectbox(t("Gespeicherte Prüfung"), list(indexed),
            format_func=lambda key: f"{indexed[key]['created_at'][:16].replace('T', ' ')} UTC · {indexed[key]['result']['claim'][:90]}",
            key="history_selected", persist_state="session")
        record = indexed[selected_id]
        with st.expander(t("Export & Verlauf verwalten"), expanded=False):
            st.caption(t("JSON enthält Ergebnis und Belegzitate; CSV eine Übersicht. PDF-Dateien sind nicht enthalten."))
            st.download_button(t("Diese Prüfung als Druckbericht (HTML)"),
                               data=render_check_report([_report_source(record)], UI_LANGUAGE),
                               file_name="faktencheck.html", mime="text/html", key="history_single_report")
            st.download_button(t("Gefilterten Verlauf als Druckbericht (HTML)"),
                               data=render_check_report([_report_source(item) for item in history], UI_LANGUAGE),
                               file_name="pruefverlauf.html", mime="text/html", key="history_report")
            st.download_button(t("Diese Prüfung als JSON"), data=export_checks([record]),
                               file_name=f"faktencheck_{record['id'][:8]}.json", mime="application/json")
            st.download_button(t("Angezeigten Verlauf als CSV"), data=export_checks(history, 'csv'),
                               file_name="pruefverlauf.csv", mime="text/csv")
            st.download_button(t("Angezeigten Verlauf als JSON"), data=export_checks(history),
                               file_name="pruefverlauf.json", mime="application/json")
            action = "Wiederherstellen" if in_trash else "In Papierkorb verschieben"
            st.caption(t("Einträge im Papierkorb bleiben lokal gespeichert und können wiederhergestellt werden. Die PDF-Datei bleibt erhalten."))
            if st.button(t(action), key="history_remove_restore"):
                try:
                    if set_removed(HISTORY_PATH, selected_id, removed=not in_trash):
                        st.session_state["history_message"] = "Prüfung wiederhergestellt." if in_trash else "Prüfung in den Papierkorb verschoben."
                        st.rerun()
                    else:
                        st.warning(t("Der Eintrag ist nicht mehr vorhanden. Bitte die Ansicht aktualisieren."))
                except (OSError, sqlite3.Error):
                    st.error(t("Der Verlauf konnte nicht geändert werden."))
        saved_result = PredictionResult(**record["result"])
        st.markdown("#### " + t(VERDICTS[saved_result.label][2]))
        st.text(saved_result.claim)
        st.caption(t('Quelle: {v0} · {v1} · Strategie: {v2}').format(v0=record['date'] or t('Eigenes PDF'), v1=record['language'].upper(), v2=saved_result.strategy))
        if record["origin"] == "saved_demo":
            st.info(t("Gespeicherte Demoantwort – bei dieser Prüfung wurde keine neue Liveantwort erzeugt."))
        else:
            st.caption(t("Gespeicherte Liveantwort · Keine erneute API-Anfrage beim Öffnen"))
        if record.get("vote"):
            st.text(t("Vorlage") + ": " + _vote_title(record["vote"]))
        source_path = matching_source(config.BOOKLETS_DIR, record)
        if source_path is None:
            st.warning(t("Die ursprüngliche PDF-Datei fehlt oder hat sich verändert. Die gespeicherten Zitate bleiben lesbar; eine Originalvorschau wird nicht angezeigt."))
        if saved_result.label == 1:
            st.info(t("Im damals geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden."))
        for index, source in enumerate(saved_result.evidence_sources if saved_result.label != 1 else []):
            with st.expander(t('Beleg {v0} · PDF-Seite {v1}').format(v0=index + 1, v1=source.page_number), expanded=index == 0):
                snippet = best_snippet(source.quote, saved_result.claim)
                st.text(snippet)
                if source_path and source.page_number:
                    try:
                        preview = original_page_preview(source_path.read_bytes(), source.page_number,
                                                        snippet.removesuffix(" …"), saved_result.label)
                        st.caption(t(preview.reason))
                        st.image(preview.png, width="stretch")
                    except Exception:
                        st.info(t("Die Originalseite lässt sich gerade nicht darstellen."))
        with st.expander(t("Gespeicherte Details"), expanded=False):
            st.json(saved_result.to_official_dict())


# =============================================================================
# SEITE 3: BENCHMARK DASHBOARD
# =============================================================================
if page == PAGES[2]:
    if st.session_state.get("ui_language", "de") != "de":
        st.info(t("Technische Forschungsnotizen und gespeicherte Benchmark-Auswertungen sind derzeit auf Deutsch dokumentiert."))
    summary = build_summary()
    head_adv = summary["headline"].get("advanced")
    head_beg = summary["headline"].get("beginner")

    # One row per configuration: keep the largest, then latest run
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in summary["runs"]:
        if r["split"] != "test":
            continue
        key = (r["task"], r["strategy"], r["top_k"], r.get("passage_chars"), r["prompt_mode"], r.get("ids_reason"))
        if key not in latest or (r["n"], r["timestamp"]) > (latest[key]["n"], latest[key]["timestamp"]):
            latest[key] = r
    test_runs = sorted(latest.values(), key=lambda r: (r["task"], -r["macro_f1"]))

    st.markdown(t("### Benchmark-Ergebnisse"))
    st.markdown(
        t("Gespeicherte OST-Auswertungen. Die bisherigen Datensatztermine einschliesslich des früheren Test-Splits "
        "sind beobachtete Dev-/Regressionsdaten und haben die Entwicklung beeinflusst. "
        "Die Zahlen sind kein unabhängiger Generalisierungsnachweis.")
    )

    dev42 = load_completed_dev42(config.BASE_DIR / "results/genai/real_nli42.json")
    if dev42:
        st.markdown(t("##### GenAI: gepaarter Dev-Vergleich mit 42 Fällen"))
        st.caption(t("Beobachtete Fälle eines Büchleindatums mit Abschnitts-Cues, kein unabhängiger Test. "
                   "Gleicher JSON-Prompt; dieser Vergleich bildet nicht die vollständige Produktions-NLI ab."))
        names = {"baseline_native": "Baseline", "vector": "Dense", "vector_hybrid": "Fusion"}
        st.dataframe([{"Suche": names[row["variant"]], "Fälle": row["count"],
                       "Macro-F1": f"{row['macro_f1']:.4f}",
                       "Evidence Hit@5": f"{row['evidence_hit_at_5']:.1%}",
                       "Ø Prompttokens": round(row["mean_prompt_tokens"])}
                      for row in dev42["summary"] if row["group"] == "all"],
                     hide_index=True, width="stretch")
        st.caption(t("Evidence Hit@5: 31 Gold-Entailment-/Contradictionfälle. "
                   "Die Baseline behält ihre Abschnittsauswahl und ihr natives Kontextbudget."))
        st.markdown(t("##### Historische gespeicherte Konfigurationen"))

    def _kpi(value: str, label: str, color: str) -> str:
        return f'<div class="kpi-card"><div class="kpi-value kpi-{color}">{value}</div><div class="kpi-label">{label}</div></div>'

    # Same 150-pair test sample for the context comparison
    def _pick(strategy: str, prompt: str, top_k: Optional[int] = None) -> Optional[Dict[str, Any]]:
        cands = [r for r in summary["runs"] if r["split"] == "test" and r["task"] == "advanced" and r["n"] == 150
                 and r["strategy"] == strategy and r["prompt_mode"] == prompt and (top_k is None or r["top_k"] == top_k)]
        return max(cands, key=lambda r: r["timestamp"]) if cands else None

    comparison = [
        ("Ganzes Büchlein", _pick("full", "json"), False),
        ("Erste Pipeline", _pick("retrieval", "json", 5), False),
        ("Hybrid (historisch)", _pick("hybrid", "ids", 10), True),
    ]
    comparison = [(name, run, ours) for name, run, ours in comparison if run]

    kpis = []
    if head_adv:
        kpis.append(_kpi(f"{head_adv['macro_f1']:.3f}", f"Macro-F1 Advanced · n={head_adv['n']}", "green"))
    if head_beg:
        kpis.append(_kpi(f"{head_beg['macro_f1']:.3f}", f"Macro-F1 Beginner · n={head_beg['n']}", "blue"))
    if head_adv:
        kpis.append(_kpi(f"{head_adv['latency_p95_ms'] / 1000:.1f} s", "p95 Antwortzeit (Advanced)", "yellow"))
    full_run = next((run for name, run, ours in comparison if name == "Ganzes Büchlein"), None)
    ours_run = next((run for name, run, ours in comparison if ours), None)
    if full_run and ours_run:
        kpis.append(_kpi(f"−{(1 - ours_run['avg_prompt_tokens'] / full_run['avg_prompt_tokens']) * 100:.0f}%",
                         "Input-Tokens vs. ganzes Büchlein", "orange"))
    if kpis:
        st.markdown(f'<div class="kpi-grid">{"".join(kpis)}</div>', unsafe_allow_html=True)
    else:
        st.info(t("Noch keine gespeicherten Regressionsläufe mit den aktuellen Standardeinstellungen in results/."))

    # ── Core finding: selected context beats the full booklet ───────────────
    if len(comparison) >= 2:
        st.markdown(t("##### Ganzes Büchlein vs. ausgewählter Kontext (Advanced-Task, gleiche 150 beobachtete Paare)"))

        def bar_chart(title: str, field: str, fmt, max_value: float, note: str) -> str:
            rows = []
            for name, run, ours in comparison:
                value = run[field]
                width = max(2.0, value / max_value * 100)
                tip = f"{name}: {fmt(value)} · Strategie {run['strategy']}, Prompt {run['prompt_mode']}, n={run['n']}"
                rows.append(
                    f'<div class="bar-row" title="{html.escape(tip)}">'
                    f'<span class="bar-name{" bar-ours" if ours else ""}">{name}</span>'
                    f'<div class="bar-track"><div class="bar-fill{" bar-fill-ours" if ours else ""}" style="width:{width:.1f}%"></div></div>'
                    f'<span class="bar-value">{fmt(value)}</span></div>'
                )
            return f'<div class="bar-chart"><div class="bar-title">{title}</div>{"".join(rows)}<div class="bar-note">{note}</div></div>'

        c_f1, c_tok = st.columns(2, gap="large")
        with c_f1:
            st.markdown(bar_chart("Macro-F1", "macro_f1", lambda v: f"{v:.3f}", 1.0, "höher ist besser"), unsafe_allow_html=True)
        with c_tok:
            max_tok = max(run["avg_prompt_tokens"] for _, run, _ in comparison)
            st.markdown(bar_chart("Ø Input-Tokens pro Behauptung", "avg_prompt_tokens",
                                  lambda v: f"{v:,.0f}".replace(",", "'"), max_tok, "tiefer ist besser"), unsafe_allow_html=True)
        st.caption(t("Historische Konfigurationen mit unterschiedlicher Kontextauswahl und unterschiedlichen Prompts. Tokenzahlen sind keine Kostenabrechnung."))

    # ── Language-pair matrix ────────────────────────────────────────────────
    if head_adv:
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.markdown(f"##### Advanced-Task nach Sprachpaar · Macro-F1 (n={head_adv['n']})")
        langs = ["de", "fr", "it"]
        names = {"de": "DE", "fr": "FR", "it": "IT"}
        cells = ['<div class="mx-corner">Behauptung ↓ · Büchlein →</div>'] + [f'<div class="mx-head">{names[b]}</div>' for b in langs]
        for c in langs:
            cells.append(f'<div class="mx-head">{names[c]}</div>')
            for b in langs:
                v = head_adv["by_language_pair"].get(f"{c}->{b}")
                if not v:
                    cells.append('<div class="mx-cell">–</div>')
                    continue
                alpha = min(0.85, max(0.12, (v["macro_f1"] - 0.7) / 0.3 * 0.85))
                tip = f"{names[c]} → {names[b]}: Macro-F1 {v['macro_f1']:.3f}, Accuracy {v['accuracy']:.3f}, n={v['count']}"
                cells.append(
                    f'<div class="mx-cell" style="background: color-mix(in srgb, var(--blue) {alpha * 40:.0f}%, var(--surface))" title="{tip}">'
                    f'<span class="mx-val">{v["macro_f1"]:.2f}</span><span class="mx-n">n={v["count"]}</span></div>'
                )
        st.markdown(f'<div class="mx-grid">{"".join(cells)}</div>', unsafe_allow_html=True)
        st.caption(t("Diagonale = gleiche Sprache. Schwächstes Paar: italienische Behauptung gegen deutsches Büchlein."))

    # ── Table view of all configurations ────────────────────────────────────
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(t("##### Alle Konfigurationen (historischer Test-Split, inzwischen beobachtet)"))
    if test_runs:
        st.dataframe(
            [
                {
                    "Task": r["task"],
                    "Kontext": r["strategy"] + (f" k={r['top_k']}" if r["strategy"] in ("hybrid", "retrieval") else ""),
                    "Prompt": r["prompt_mode"],
                    "n": r["n"],
                    "Macro-F1": round(r["macro_f1"], 3),
                    "Cross-lingual F1": round(r["macro_f1_crosslingual"], 3) if r["macro_f1_crosslingual"] is not None else None,
                    "Ø Input-Tokens": int(r["avg_prompt_tokens"]),
                    "Ø Output-Tokens": int(r["avg_completion_tokens"]),
                    "p95 Latenz (s)": round(r["latency_p95_ms"] / 1000, 1),
                }
                for r in test_runs
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(t("Latenz clientseitig gemessen; frühe Läufe teilten sich den Endpunkt mit anderen Läufen und sind dadurch langsamer."))

    # Live Evaluation
    with st.expander(t("Live-Benchmark ausführen"), expanded=False):
        datasets = {"OST Regression (früherer Test-Split)": config.DATA_DIR / "hf" / "test.jsonl", "OST Dev-Split": config.DATA_DIR / "hf" / "dev.jsonl", "Demo (Juni 2026)": config.BENCHMARK_PATH}
        datasets = {k: v for k, v in datasets.items() if v.exists()}
        c_ds, c_task, c_strat, c_lim = st.columns([2, 1, 1, 1])
        with c_ds:
            eval_dataset = st.selectbox(t("Datensatz"), list(datasets), key="eval_dataset", persist_state="session")
        with c_task:
            eval_task = st.selectbox(t("Task"), ["advanced", "beginner"], key="eval_task", persist_state="session")
        with c_strat:
            eval_strat = st.selectbox(t("Strategie"), ["hybrid", "retrieval", "full"], key="eval_strat", persist_state="session")
        with c_lim:
            eval_lim = st.selectbox(t("Limit"), [6, 15, 30, "Alle"], key="eval_lim", persist_state="session")

        if st.button(t("Evaluierung starten"), key="btn_run_live_eval"):
            lim_val = None if eval_lim == "Alle" else int(eval_lim)
            with st.spinner(t('Evaluiere {v0} Samples…').format(v0=eval_lim)):
                evaluator = BenchmarkEvaluator()
                live_report = evaluator.evaluate(
                    dataset_path=datasets[eval_dataset], strategy=eval_strat, task=eval_task, limit=lim_val, save=False,
                )

            st.success(t('Abgeschlossen — {v0} Samples').format(v0=live_report['sample_count']))
            lr1, lr2, lr3, lr4 = st.columns(4)
            lr1.metric("Macro-F1", f"{live_report['macro_f1']:.4f}")
            grounded = live_report["evidence"]["evidence_grounded_rate"]
            lr2.metric("Belege im Gold-Abschnitt", f"{grounded * 100:.0f}%" if grounded is not None else "–")
            lr3.metric("Ø Input-Tokens", f"{live_report['efficiency']['avg_prompt_tokens']:.0f}")
            lr4.metric("p95 Latenz", f"{live_report['efficiency']['latency_p95_ms']:.0f} ms")

            st.dataframe(
                [
                    {
                        "Behauptung": r["claim"][:80] + "...",
                        "Sprachpaar": r["pair"].upper(),
                        "True": r["true_label"],
                        "Pred": r["pred_label"],
                        "✓": "✓" if r["correct"] else "✗",
                        "Tokens": r["tokens_prompt"] + r["tokens_completion"],
                        "Latenz": f"{r['latency_ms']:.0f} ms",
                    }
                    for r in live_report["results"]
                ],
                width="stretch",
                hide_index=True,
            )


# =============================================================================
# SEITE 4: ARCHITEKTUR
# =============================================================================
if page == PAGES[3]:
    if st.session_state.get("ui_language", "de") != "de":
        st.info(t("Technische Forschungsnotizen und gespeicherte Benchmark-Auswertungen sind derzeit auf Deutsch dokumentiert."))
    st.markdown(t("### So funktioniert Fact Attack"))

    st.markdown("""
    <div class="flow">
        <div class="flow-step">
            <div class="flow-num">1</div>
            <div class="flow-title">Büchlein lesen</div>
            <div class="flow-text">PDF in Seiten zerlegen, jede Seite mit ihrem Abschnitt markieren: Komitee, Bundesrat, Abstimmungstext …</div>
        </div>
        <div class="flow-arrow">→</div>
        <div class="flow-step">
            <div class="flow-num">2</div>
            <div class="flow-title">Relevante Seiten finden</div>
            <div class="flow-text">BM25 über alle Seiten: Behauptung + 2 × Titel der Vorlage. Die 10 besten Seiten gehen weiter.</div>
        </div>
        <div class="flow-arrow">→</div>
        <div class="flow-step flow-step-apertus">
            <div class="flow-num">3</div>
            <div class="flow-title">Apertus entscheidet</div>
            <div class="flow-text">Seiten nummeriert [P1]…[P10]. Apertus nennt Label und die Nummern der Belegseiten (~55 Tokens).</div>
        </div>
        <div class="flow-arrow">→</div>
        <div class="flow-step">
            <div class="flow-num">4</div>
            <div class="flow-title">Beleg anzeigen</div>
            <div class="flow-text">Label 0 / 1 / 2, wörtliche Belegstellen mit Seitenzahl, Tokens und Antwortzeit.</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    col_a1, col_a2 = st.columns(2, gap="large")
    with col_a1:
        st.markdown(t("##### Warum der Titel der Vorlage?"))
        st.markdown(
            t("Der Vorlagentitel steht in der Sprache des Büchleins. Er findet die richtige Stelle auch dann, wenn die "
            "Behauptung auf Italienisch und das Büchlein auf Deutsch ist. Ein harter Filter auf eine erkannte Vorlage "
            "verlor dagegen die Übersichtsseiten vorne im Büchlein. Trefferquote des Gold-Abschnitts: 0.74 → 0.93.")
        )
        st.markdown(t("##### Warum Seitennummern statt Zitate?"))
        st.markdown(
            t("Apertus nennt nur die Nummern der Belegseiten. So sind Belege immer wörtlicher Text aus dem Büchlein mit "
            "korrekter Seitenzahl, und die Antwort ist rund dreimal kürzer als mit Begründung und Zitaten.")
        )
    with col_a2:
        st.markdown(t("##### Warum Abschnitte?"))
        st.markdown(
            t("Ein Büchlein enthält bewusst beide Seiten. «Das Komitee argumentiert …» wird nur am Text des Komitees "
            "geprüft — Gegenargumente des Bundesrats sind kein Widerspruch.")
        )
        st.markdown(t("##### Was wir verworfen haben"))
        st.markdown(
            t("- **Ganzes Büchlein senden:** F1 0.73 bei ~60'000 Tokens\n"
            "- **Harter Zahlen-Override:** 5 von 5 Eingriffen falsch\n"
            "- **Nur Label-Ziffer abfragen:** schwach bei «Neutral» (F1 0.72)\n"
            "- **Kleine Textstücke statt Seiten:** präzisere Belege, aber F1 0.83 statt 0.91\n"
            "- **Zweitprüfung bei «Widerspruch»:** 0 von 23 Fehlern behoben, 24 neue\n"
            "- **Apertus 8B statt 70B:** 3,5× schneller, aber F1 0.85 statt 0.91")
        )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(t("##### Reproduzierbarkeit"))
    st.code("""# OST-Datensatz, alle Büchlein und Dev/Test-Split laden
python -m src.hf_dataset

# Advanced-Task (Büchlein + Behauptung + Vorlage) auf dem Test-Split
python -m src benchmark -d data/hf/test.jsonl -t advanced

# Beginner-Task (Behauptung + Referenztext)
python -m src benchmark -d data/hf/test.jsonl -t beginner""", language="bash")

with recent_slot:
    _draw_recent_checks()

# ── Footer ───────────────────────────────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.caption(t("Fact Attack · GenAI-Modulprojekt · Team Fact Attack · [Repository](https://github.com/SheylaSam/fact-attack-genai) · [Originalprojekt](https://github.com/JJukic/Apertus-Team-Fact-Attack-2026)"))
