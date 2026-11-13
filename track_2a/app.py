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
from src import agent_app, agent_ui  # without langgraph: the agent layer itself is imported only on the agent path

# Page Configuration
st.set_page_config(
    page_title="Fact Attack 2026",
    page_icon="🇨🇭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

UI_LANGUAGE = st.session_state.get("ui_language", "de")
# Streamlit keeps the wire label of keyed selections; resend canonical values
# when only their display language changes.
if st.session_state.get("_last_ui_language") != UI_LANGUAGE:
    for selection_key in ("source_year", "ballot", "vote", "claim_lang", "strategy_option", "history_view", "history_label", "history_origin", "history_order"):
        if selection_key in st.session_state:
            st.session_state[selection_key] = st.session_state[selection_key]
    st.session_state["_last_ui_language"] = UI_LANGUAGE


def t(text):
    return translate(text, UI_LANGUAGE)


def th(markup):
    return translate_static_html(markup, UI_LANGUAGE)


# =============================================================================
# DESIGN SYSTEM — Premium minimal Swiss-inspired dark UI
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

    /* ══════════════════════════════════════════════════════════════
       GLOBAL DARK THEME — Forces dark on EVERY Streamlit widget
       ══════════════════════════════════════════════════════════════ */

    /* ── Reset & Base ──────────────────────────────────────────── */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        color: #e4e8ef;
    }

    .stApp {
        background: #08090c !important;
        color: #e4e8ef !important;
    }

    /* Force dark on all Streamlit internal containers */
    .stApp > div,
    .stApp > div > div,
    section[data-testid="stSidebar"],
    div[data-testid="stAppViewBlockContainer"] {
        background-color: transparent !important;
    }

    /* Hide Streamlit header chrome & footer */
    header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {
        display: none !important;
    }
    footer, #MainMenu { display: none !important; }

    .block-container {
        padding-top: 1.6rem !important;
        padding-bottom: 2rem !important;
        max-width: 1240px !important;
    }

    /* ── Ambient glow (top-of-page accent) ─────────────────────── */
    .ambient-glow {
        position: fixed;
        top: -120px;
        left: 50%;
        transform: translateX(-50%);
        width: 600px;
        height: 320px;
        background: radial-gradient(ellipse, rgba(213,43,30,0.08) 0%, transparent 70%);
        pointer-events: none;
        z-index: 0;
    }

    /* ══════════════════════════════════════════════════════════════
       STREAMLIT WIDGET OVERRIDES — Comprehensive dark theme
       ══════════════════════════════════════════════════════════════ */

    /* ── ALL Text Inputs (input, textarea) & BaseWeb Wrappers ───── */
    div[data-baseweb="base-input"],
    div[data-baseweb="input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="base-input"] > div,
    div[data-baseweb="textarea"] > div,
    [data-testid="stTextInputRootElement"],
    [data-testid="stTextArea"] > div,
    [data-testid="stTextInput"] > div {
        background-color: #12151c !important;
        background: #12151c !important;
        border-color: rgba(255,255,255,0.12) !important;
        border-radius: 8px !important;
    }

    input, textarea,
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        background: #12151c !important;
        background-color: #12151c !important;
        border: 1px solid rgba(255,255,255,0.12) !important;
        border-radius: 8px !important;
        color: #f1f5f9 !important;
        -webkit-text-fill-color: #f1f5f9 !important;
        caret-color: #d52b1e !important;
        font-size: 0.95rem !important;
        line-height: 1.5 !important;
    }
    input:focus, textarea:focus,
    div[data-baseweb="base-input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within {
        border-color: rgba(213,43,30,0.6) !important;
        box-shadow: 0 0 0 2px rgba(213,43,30,0.15) !important;
        outline: none !important;
        background-color: #141822 !important;
    }
    input::placeholder, textarea::placeholder {
        color: #64748b !important;
        -webkit-text-fill-color: #64748b !important;
    }
    textarea {
        border-radius: 10px !important;
    }

    /* ── Selectbox (dropdown trigger) ──────────────────────────── */
    [data-testid="stSelectbox"] > div > div {
        background: #12151c !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 8px !important;
        color: #e2e8f0 !important;
    }
    [data-testid="stSelectbox"] > div > div:hover {
        border-color: rgba(255,255,255,0.18) !important;
    }
    [data-testid="stSelectbox"] svg {
        fill: #94a3b8 !important;
    }
    [data-testid="stSelectbox"] label {
        color: #94a3b8 !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
    }

    /* ── Selectbox Dropdown Popup (listbox) ────────────────────── */
    div[data-baseweb="popover"] {
        background: #12151c !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 8px !important;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5) !important;
    }
    div[data-baseweb="popover"] ul {
        background: #12151c !important;
    }
    div[data-baseweb="popover"] li {
        background: transparent !important;
        color: #cbd5e1 !important;
    }
    div[data-baseweb="popover"] li:hover {
        background: rgba(255,255,255,0.06) !important;
        color: #f1f5f9 !important;
    }
    div[data-baseweb="popover"] li[aria-selected="true"] {
        background: rgba(213,43,30,0.15) !important;
        color: #fca5a5 !important;
    }

    /* ── BaseWeb Select internals ─────────────────────────────── */
    div[data-baseweb="select"] > div {
        background: #12151c !important;
        border-color: rgba(255,255,255,0.1) !important;
        color: #e2e8f0 !important;
    }
    div[data-baseweb="select"] > div:hover {
        border-color: rgba(255,255,255,0.18) !important;
    }
    div[data-baseweb="select"] span {
        color: #e2e8f0 !important;
    }
    div[data-baseweb="select"] svg {
        fill: #94a3b8 !important;
        color: #94a3b8 !important;
    }
    div[data-baseweb="select"] input {
        color: #e2e8f0 !important;
    }
    /* Dropdown menu container */
    div[data-baseweb="menu"] {
        background: #12151c !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
    }
    div[data-baseweb="menu"] li {
        background: transparent !important;
        color: #cbd5e1 !important;
    }
    div[data-baseweb="menu"] li:hover {
        background: rgba(255,255,255,0.06) !important;
    }

    /* ── Multiselect ──────────────────────────────────────────── */
    [data-testid="stMultiSelect"] > div > div {
        background: #12151c !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        color: #e2e8f0 !important;
    }
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        background: rgba(213,43,30,0.15) !important;
        color: #fca5a5 !important;
        border: 1px solid rgba(213,43,30,0.3) !important;
    }

    /* ── Radio Buttons ────────────────────────────────────────── */
    .stRadio label, .stSelectbox label, .stTextArea label,
    [data-testid="stWidgetLabel"] label,
    [data-testid="stWidgetLabel"] p {
        color: #94a3b8 !important;
        font-weight: 600 !important;
        font-size: 0.85rem !important;
    }
    .stRadio div[role="radiogroup"] label {
        color: #cbd5e1 !important;
        font-size: 0.85rem !important;
    }
    .stRadio div[role="radiogroup"] label:hover {
        color: #f1f5f9 !important;
    }
    /* Radio button circle */
    .stRadio div[role="radiogroup"] label > div:first-child {
        border-color: rgba(255,255,255,0.2) !important;
    }

    /* ── Checkbox ─────────────────────────────────────────────── */
    .stCheckbox label {
        color: #cbd5e1 !important;
    }
    .stCheckbox label > span:first-child {
        border-color: rgba(255,255,255,0.2) !important;
        background: transparent !important;
    }

    /* ── Slider ───────────────────────────────────────────────── */
    [data-testid="stSlider"] label {
        color: #94a3b8 !important;
    }
    [data-testid="stSlider"] div[data-baseweb="slider"] div {
        background: rgba(255,255,255,0.08) !important;
    }

    /* ── Number Input ─────────────────────────────────────────── */
    [data-testid="stNumberInput"] input {
        background: #12151c !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        color: #e2e8f0 !important;
    }
    [data-testid="stNumberInput"] button {
        background: rgba(255,255,255,0.04) !important;
        color: #94a3b8 !important;
        border-color: rgba(255,255,255,0.1) !important;
    }

    /* ── Tabs ─────────────────────────────────────────────────── */
    [data-testid="stTabs"] {
        background: transparent !important;
    }
    [data-testid="stTabs"] button {
        font-weight: 600 !important;
        font-size: 0.88rem !important;
        color: #64748b !important;
        border-bottom-color: transparent !important;
        padding: 0.6rem 1rem !important;
        background: transparent !important;
    }
    [data-testid="stTabs"] button:hover {
        color: #94a3b8 !important;
    }
    [data-testid="stTabs"] button[aria-selected="true"] {
        color: #f1f5f9 !important;
        border-bottom-color: #d52b1e !important;
        background: transparent !important;
    }

    /* ── Expander ─────────────────────────────────────────────── */
    [data-testid="stExpander"],
    details[data-testid="stExpander"] {
        background: #0f1219 !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
        margin-bottom: 0.8rem !important;
        box-shadow: none !important;
        overflow: hidden !important;
    }
    [data-testid="stExpander"] summary,
    details[data-testid="stExpander"] summary,
    [data-testid="stExpanderToggleIcon"] {
        background-color: #0f1219 !important;
        color: #cbd5e1 !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
    }
    [data-testid="stExpander"] summary:hover,
    details[data-testid="stExpander"] summary:hover {
        background-color: #141822 !important;
        color: #ffffff !important;
    }
    [data-testid="stExpander"] summary svg,
    details[data-testid="stExpander"] summary svg {
        color: #94a3b8 !important;
        fill: #94a3b8 !important;
    }
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
        background: #0a0d13 !important;
        border-top: 1px solid rgba(255,255,255,0.05) !important;
        padding: 1.2rem !important;
    }
    [data-testid="stExpander"] > div > div {
        background: transparent !important;
    }

    /* ── File Uploader ────────────────────────────────────────── */
    [data-testid="stFileUploader"] { background: transparent !important; }
    [data-testid="stFileUploaderDropzone"] {
        background: #12151c !important;
        border: 1px dashed rgba(255,255,255,0.12) !important;
        border-radius: 10px !important;
        padding: 1rem !important;
    }
    [data-testid="stFileUploaderDropzone"] div,
    [data-testid="stFileUploaderDropzone"] span,
    [data-testid="stFileUploaderDropzone"] small,
    [data-testid="stFileUploaderDropzone"] p {
        color: #64748b !important;
    }
    [data-testid="stFileUploaderDropzone"] button {
        background: rgba(255,255,255,0.04) !important;
        color: #94a3b8 !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 6px !important;
    }

    /* ── Primary Button ───────────────────────────────────────── */
    button[kind="primary"] {
        background: #d52b1e !important;
        color: #fff !important;
        font-weight: 700 !important;
        border: none !important;
        border-radius: 8px !important;
        box-shadow: 0 4px 14px rgba(213,43,30,0.3), 0 0 0 1px rgba(213,43,30,0.5) !important;
        transition: all 0.15s ease !important;
        letter-spacing: 0.01em !important;
    }
    button[kind="primary"] p, button[kind="primary"] div, button[kind="primary"] span {
        color: #fff !important;
        -webkit-text-fill-color: #fff !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
    }
    button[kind="primary"]:hover {
        background: #b91c1c !important;
        box-shadow: 0 6px 20px rgba(213,43,30,0.45), 0 0 0 1px rgba(213,43,30,0.6) !important;
        transform: translateY(-1px) !important;
    }

    /* ── Secondary / All Other Buttons ─────────────────────────── */
    button[kind="secondary"],
    .stButton > button:not([kind="primary"]),
    .stDownloadButton > button {
        background: rgba(255,255,255,0.04) !important;
        color: #cbd5e1 !important;
        border: 1px solid rgba(255,255,255,0.1) !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
        transition: all 0.15s ease !important;
    }
    button[kind="secondary"]:hover,
    .stButton > button:not([kind="primary"]):hover,
    .stDownloadButton > button:hover {
        background: rgba(255,255,255,0.08) !important;
        color: #f1f5f9 !important;
        border-color: rgba(255,255,255,0.16) !important;
    }

    /* ── Alert Boxes (st.success, st.info, st.warning, st.error) ─ */
    [data-testid="stAlert"],
    .stAlert,
    div[role="alert"] {
        background: rgba(255,255,255,0.03) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        color: #cbd5e1 !important;
        border-radius: 8px !important;
    }
    [data-testid="stAlert"] p,
    .stAlert p,
    div[role="alert"] p {
        color: #cbd5e1 !important;
    }
    /* Success variant */
    div[data-testid="stAlert"][data-baseweb*="positive"],
    .element-container .stSuccess {
        border-left: 3px solid #10b981 !important;
    }
    /* Info variant */
    .stInfo, [data-testid="stNotification"] {
        background: rgba(59,130,246,0.06) !important;
        border: 1px solid rgba(59,130,246,0.15) !important;
        color: #93c5fd !important;
        border-radius: 8px !important;
    }
    .stInfo p, [data-testid="stNotification"] p {
        color: #93c5fd !important;
    }

    /* ── Metric Widget ────────────────────────────────────────── */
    [data-testid="stMetric"],
    [data-testid="stMetricValue"],
    [data-testid="metric-container"] {
        background: transparent !important;
    }
    [data-testid="stMetric"] label,
    [data-testid="stMetric"] [data-testid="stMetricLabel"] {
        color: #64748b !important;
        font-size: 0.78rem !important;
        font-weight: 600 !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #e2e8f0 !important;
        font-weight: 700 !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricDelta"] {
        color: #94a3b8 !important;
    }

    /* ── Spinner ──────────────────────────────────────────────── */
    .stSpinner > div {
        background: transparent !important;
        color: #94a3b8 !important;
    }
    .stSpinner > div > div {
        border-top-color: #d52b1e !important;
    }

    /* ── Caption ──────────────────────────────────────────────── */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: #475569 !important;
    }
    .stCaption p, [data-testid="stCaptionContainer"] p {
        color: #475569 !important;
    }

    /* ── Markdown Text ────────────────────────────────────────── */
    .stMarkdown p, .stMarkdown li, .stMarkdown h1,
    .stMarkdown h2, .stMarkdown h3, .stMarkdown h4,
    .stMarkdown h5, .stMarkdown h6 {
        color: #e2e8f0 !important;
    }
    .stMarkdown a {
        color: #60a5fa !important;
    }
    .stMarkdown code {
        background: rgba(255,255,255,0.06) !important;
        color: #f0abfc !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
    }
    .stMarkdown pre {
        background: #0c0e14 !important;
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 8px !important;
    }
    .stMarkdown pre code {
        background: transparent !important;
        color: #cbd5e1 !important;
    }
    /* LaTeX / KaTeX */
    .katex, .katex .mord, .katex .mbin, .katex .mrel,
    .katex .mopen, .katex .mclose, .katex .mpunct, .katex .minner {
        color: #e2e8f0 !important;
    }

    /* ── Table ────────────────────────────────────────────────── */
    [data-testid="stTable"] {
        border-radius: 10px;
        overflow: hidden;
    }
    [data-testid="stTable"] table {
        background: transparent !important;
    }
    [data-testid="stTable"] th {
        background: rgba(255,255,255,0.04) !important;
        color: #64748b !important;
        font-size: 0.72rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
        border-color: rgba(255,255,255,0.05) !important;
    }
    [data-testid="stTable"] td {
        background: rgba(255,255,255,0.015) !important;
        color: #cbd5e1 !important;
        font-size: 0.85rem !important;
        border-color: rgba(255,255,255,0.04) !important;
    }

    /* ── DataFrame / DataEditor ───────────────────────────────── */
    [data-testid="stDataFrame"],
    [data-testid="stDataFrame"] > div,
    .stDataFrame {
        background: transparent !important;
    }
    [data-testid="stDataFrame"] [data-testid="glideDataEditor"] {
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 8px !important;
    }

    /* ── Progress bar ─────────────────────────────────────────── */
    [data-testid="stProgressBar"] > div > div {
        background: rgba(255,255,255,0.04) !important;
        border-radius: 3px !important;
    }

    /* ── Code Block ───────────────────────────────────────────── */
    [data-testid="stCode"],
    .stCodeBlock {
        background: #0c0e14 !important;
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 8px !important;
    }
    [data-testid="stCode"] pre,
    .stCodeBlock pre {
        background: transparent !important;
        color: #cbd5e1 !important;
    }
    /* Copy button in code blocks */
    [data-testid="stCode"] button,
    .stCodeBlock button {
        color: #64748b !important;
        background: rgba(255,255,255,0.04) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
    }

    /* ── Tooltip ──────────────────────────────────────────────── */
    [data-testid="stTooltipIcon"] {
        color: #475569 !important;
    }

    /* ── Divider (st.divider / hr) ────────────────────────────── */
    hr {
        border-color: rgba(255,255,255,0.06) !important;
    }

    /* ── Scrollbar ────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.08); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.14); }


    /* ══════════════════════════════════════════════════════════════
       CUSTOM COMPONENT STYLES
       ══════════════════════════════════════════════════════════════ */

    /* ── Top Bar ───────────────────────────────────────────────── */
    .topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.4rem 0;
        margin-bottom: 1rem;
        border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .topbar-brand {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .topbar-brand .logo-mark {
        width: 28px;
        height: 28px;
        background: #d52b1e;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 14px;
        font-weight: 800;
        color: #fff;
        box-shadow: 0 0 16px rgba(213,43,30,0.35);
    }
    .topbar-brand .brand-text {
        font-size: 0.95rem;
        font-weight: 700;
        letter-spacing: -0.01em;
        color: #f1f5f9;
    }
    .topbar-meta {
        display: flex;
        align-items: center;
        gap: 16px;
    }
    .topbar-tag {
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #64748b;
        padding: 3px 10px;
        border: 1px solid rgba(100,116,139,0.25);
        border-radius: 4px;
    }
    .topbar-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #10b981;
        box-shadow: 0 0 8px rgba(16,185,129,0.6);
    }

    /* ── Hero Title ────────────────────────────────────────────── */
    .hero-section {
        text-align: left;
        margin: 0.2rem 0 1.6rem 0;
    }
    .hero-section h1 {
        font-size: 1.7rem;
        font-weight: 900;
        letter-spacing: -0.035em;
        line-height: 1.15;
        margin: 0 0 0.6rem 0;
        background: linear-gradient(135deg, #ffffff 30%, #94a3b8 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .hero-section p {
        font-size: 1.05rem;
        color: #64748b !important;
        font-weight: 400;
        line-height: 1.6;
        max-width: 560px;
        margin: 0;
        -webkit-text-fill-color: #64748b !important;
    }
    .hero-section p strong {
        color: #94a3b8 !important;
        -webkit-text-fill-color: #94a3b8 !important;
        font-weight: 600;
    }

    /* ── Claim Input Label ─────────────────────────────────────── */
    .claim-stage-label {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #475569;
        margin-bottom: 0.7rem;
    }

    /* ── Verdict Display ───────────────────────────────────────── */
    .verdict-container {
        border-radius: 16px;
        padding: 2rem 2.2rem;
        margin-bottom: 1.2rem;
        position: relative;
        overflow: hidden;
    }
    .verdict-container::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
    }
    .verdict-entailment {
        background: rgba(16, 185, 129, 0.06);
        border: 1px solid rgba(16, 185, 129, 0.2);
    }
    .verdict-entailment::before { background: #10b981; }
    .verdict-neutral {
        background: rgba(245, 158, 11, 0.06);
        border: 1px solid rgba(245, 158, 11, 0.2);
    }
    .verdict-neutral::before { background: #f59e0b; }
    .verdict-contradiction {
        background: rgba(239, 68, 68, 0.06);
        border: 1px solid rgba(239, 68, 68, 0.2);
    }
    .verdict-contradiction::before { background: #ef4444; }

    .verdict-label {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        margin-bottom: 0.6rem;
    }
    .verdict-label-entailment { color: #34d399 !important; -webkit-text-fill-color: #34d399 !important; }
    .verdict-label-neutral    { color: #fbbf24 !important; -webkit-text-fill-color: #fbbf24 !important; }
    .verdict-label-contradiction { color: #f87171 !important; -webkit-text-fill-color: #f87171 !important; }

    .verdict-icon {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 800;
    }
    .icon-entailment { background: rgba(16,185,129,0.2); color: #34d399; }
    .icon-neutral    { background: rgba(245,158,11,0.2); color: #fbbf24; }
    .icon-contradiction { background: rgba(239,68,68,0.2); color: #f87171; }

    .verdict-title {
        font-size: 1.5rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        line-height: 1.3;
        color: #f8fafc !important;
        -webkit-text-fill-color: #f8fafc !important;
        margin: 0 0 0.4rem 0;
    }
    .verdict-reasoning {
        font-size: 0.92rem;
        color: #94a3b8 !important;
        -webkit-text-fill-color: #94a3b8 !important;
        line-height: 1.6;
        margin: 0;
    }

    /* ── Guardrail Banner ──────────────────────────────────────── */
    .guardrail-banner {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0.7rem 1rem;
        border-radius: 8px;
        margin-bottom: 1.2rem;
        font-size: 0.82rem;
    }
    .guardrail-ok {
        background: rgba(16,185,129,0.06);
        border: 1px solid rgba(16,185,129,0.15);
        color: #6ee7b7;
    }
    .guardrail-num {
        background: rgba(239,68,68,0.06);
        border: 1px solid rgba(239,68,68,0.15);
        color: #fca5a5;
    }
    .guardrail-vac {
        background: rgba(245,158,11,0.06);
        border: 1px solid rgba(245,158,11,0.15);
        color: #fde68a;
    }
    .guardrail-icon { font-size: 1.1rem; flex-shrink: 0; }
    .guardrail-text strong { font-weight: 700; }

    /* ── Evidence Block ────────────────────────────────────────── */
    .evidence-block {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 1.4rem 1.6rem;
        margin-bottom: 1rem;
    }
    .evidence-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 0.8rem;
    }
    .evidence-tag {
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        padding: 3px 8px;
        border-radius: 4px;
    }
    .tag-page {
        background: rgba(213,43,30,0.12);
        color: #fca5a5;
        border: 1px solid rgba(213,43,30,0.3);
    }
    .tag-proposal {
        background: rgba(59,130,246,0.12);
        color: #93c5fd;
        border: 1px solid rgba(59,130,246,0.3);
    }
    .evidence-quote {
        font-size: 1rem;
        line-height: 1.65;
        color: #e2e8f0;
        border-left: 3px solid #d52b1e;
        padding-left: 1rem;
        margin: 0;
        font-style: italic;
    }

    /* ── Comparison bars (benchmark) ───────────────────────────── */
    .bar-chart { padding: 1rem 0 0.4rem 0; }
    .bar-title { font-size: 0.8rem; font-weight: 700; color: #e2e8f0; margin-bottom: 0.7rem; }
    .bar-row { display: grid; grid-template-columns: 150px 1fr 70px; align-items: center; gap: 10px; margin: 8px 0; cursor: default; }
    .bar-name { font-size: 0.82rem; color: #94a3b8; }
    .bar-name.bar-ours { color: #f8fafc; font-weight: 700; }
    .bar-track { height: 18px; background: rgba(255,255,255,0.03); border-radius: 4px; }
    .bar-fill { height: 100%; background: #64748b; border-radius: 0 4px 4px 0; }
    .bar-fill-ours { background: #3987e5; }
    .bar-row:hover .bar-fill { filter: brightness(1.25); }
    .bar-value { font-size: 0.85rem; font-weight: 700; color: #e2e8f0; text-align: right; font-variant-numeric: tabular-nums; }
    .bar-note { font-size: 0.72rem; color: #64748b; margin-top: 0.4rem; }

    /* ── Language-pair matrix ──────────────────────────────────── */
    .mx-grid { display: grid; grid-template-columns: 170px repeat(3, 1fr); gap: 2px; max-width: 640px; margin: 0.6rem 0; }
    .mx-corner { font-size: 0.7rem; color: #64748b; display: flex; align-items: center; }
    .mx-head { font-size: 0.8rem; font-weight: 700; color: #cbd5e1; display: flex; align-items: center; justify-content: center; padding: 6px; }
    .mx-cell { border-radius: 4px; padding: 10px 6px; text-align: center; display: flex; flex-direction: column; gap: 2px; cursor: default; }
    .mx-cell:hover { outline: 2px solid rgba(255,255,255,0.5); }
    .mx-val { font-size: 1.05rem; font-weight: 800; color: #ffffff; font-variant-numeric: tabular-nums; }
    .mx-n { font-size: 0.68rem; color: #e2e8f0; opacity: 0.8; }

    /* ── Architecture flow ─────────────────────────────────────── */
    .flow { display: flex; align-items: stretch; gap: 10px; margin: 1rem 0 0.5rem 0; }
    .flow-step { flex: 1; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 1rem 1.1rem; background: rgba(255,255,255,0.02); }
    .flow-step-apertus { border-color: rgba(213,43,30,0.5); background: rgba(213,43,30,0.06); }
    .flow-num { width: 24px; height: 24px; border-radius: 50%; background: #d52b1e; color: #fff; font-size: 0.75rem; font-weight: 800; display: flex; align-items: center; justify-content: center; margin-bottom: 0.6rem; }
    .flow-title { font-size: 0.95rem; font-weight: 700; color: #f8fafc; margin-bottom: 0.35rem; }
    .flow-text { font-size: 0.8rem; color: #94a3b8; line-height: 1.5; }
    .flow-arrow { display: flex; align-items: center; color: #475569; font-size: 1.3rem; }
    .tag-section {
        background: rgba(148,163,184,0.10);
        color: #cbd5e1;
        border: 1px solid rgba(148,163,184,0.25);
    }

    /* ── Efficiency line, page chips, story, notices ───────────── */
    .eff-line {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem 1.4rem;
        font-size: 0.85rem;
        color: #94a3b8;
        margin: -0.4rem 0 1.2rem 0.2rem;
    }
    .eff-line strong { color: #e2e8f0; font-weight: 700; }
    .page-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 0.6rem; }
    .page-chip {
        font-size: 0.75rem;
        padding: 3px 9px;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.10);
        color: #cbd5e1;
    }
    .story-desc {
        font-size: 0.88rem;
        line-height: 1.5;
        color: #cbd5e1;
        margin: 0.3rem 0 1rem 0;
        padding: 0.6rem 0.8rem;
        border-left: 3px solid #d52b1e;
        background: rgba(255,255,255,0.02);
        border-radius: 0 6px 6px 0;
    }
    .story-desc strong { color: #f8fafc; }

    /* ── Verdict stats (efficiency per check) ──────────────────── */
    .verdict-stats {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0.8rem;
        margin-top: 1.3rem;
        padding-top: 1.1rem;
        border-top: 1px solid rgba(255,255,255,0.08);
    }
    .verdict-stats > div { display: flex; flex-direction: column; gap: 2px; }
    .vs-num { font-size: 1.15rem; font-weight: 800; color: #f8fafc; letter-spacing: -0.01em; }
    .vs-lbl { font-size: 0.72rem; color: #94a3b8; line-height: 1.3; }

    /* ── Result placeholder ────────────────────────────────────── */
    .result-placeholder {
        border: 1px dashed rgba(255,255,255,0.12);
        border-radius: 16px;
        padding: 1.8rem 2rem;
        margin-top: 0;
    }
    .outcome-row { display: flex; align-items: center; gap: 10px; margin: 0.7rem 0; color: #cbd5e1; font-size: 0.92rem; }
    .outcome-row strong { color: #f8fafc; }
    .outcome-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
    .dot-entailment { background: #10b981; }
    .dot-neutral { background: #f59e0b; }
    .dot-contradiction { background: #ef4444; }
    .notice-box {
        padding: 0.7rem 1rem;
        border-radius: 8px;
        margin-bottom: 1rem;
        font-size: 0.85rem;
        background: rgba(59,130,246,0.07);
        border: 1px solid rgba(59,130,246,0.2);
        color: #bfdbfe;
    }
    .disclaimer {
        font-size: 0.78rem;
        color: #64748b;
        margin-top: 0.4rem;
    }
    .agent-step { font-size: 0.9rem; padding: 0.25rem 0; color: #cbd5e1; }
    .agent-step-done::first-letter { color: #10b981; }
    .agent-step-ask { color: #fbbf24; }
    .agent-step-running { color: #94a3b8; font-style: italic; }
    .agent-step-error { color: #f87171; }

    /* ── Document Facsimile ────────────────────────────────────── */
    .doc-facsimile {
        background: #0c0e14;
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 10px;
        padding: 1.2rem 1.4rem;
        max-height: 340px;
        overflow-y: auto;
        font-size: 0.88rem;
        line-height: 1.65;
        color: #94a3b8;
    }
    .doc-facsimile-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #475569;
        padding-bottom: 0.6rem;
        margin-bottom: 0.8rem;
        border-bottom: 1px solid rgba(255,255,255,0.05);
    }
    mark.hl-entailment {
        background: rgba(16,185,129,0.35);
        color: #fff;
        font-weight: 600;
        padding: 2px 5px;
        border-radius: 3px;
        border-bottom: 2px solid #10b981;
    }
    mark.hl-contradiction {
        background: rgba(239,68,68,0.35);
        color: #fff;
        font-weight: 600;
        padding: 2px 5px;
        border-radius: 3px;
        border-bottom: 2px solid #ef4444;
    }
    mark.hl-neutral {
        background: rgba(245,158,11,0.35);
        color: #fff;
        font-weight: 600;
        padding: 2px 5px;
        border-radius: 3px;
        border-bottom: 2px solid #f59e0b;
    }

    /* ── Confidence Meter ──────────────────────────────────────── */
    .conf-row {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 0.45rem;
    }
    .conf-label {
        font-size: 0.75rem;
        font-weight: 600;
        color: #64748b;
        width: 100px;
        text-align: right;
        flex-shrink: 0;
    }
    .conf-bar-track {
        flex: 1;
        height: 6px;
        background: rgba(255,255,255,0.04);
        border-radius: 3px;
        overflow: hidden;
    }
    .conf-bar-fill {
        height: 100%;
        border-radius: 3px;
        transition: width 0.6s ease;
    }
    .conf-value {
        font-size: 0.75rem;
        font-weight: 700;
        color: #cbd5e1;
        width: 50px;
        flex-shrink: 0;
    }

    /* ── KPI Grid (Benchmark) ─────────────────────────────────── */
    .kpi-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 12px;
        margin-bottom: 1.5rem;
    }
    .kpi-card {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 1.2rem 1rem;
        text-align: center;
    }
    .kpi-value {
        font-size: 1.8rem;
        font-weight: 800;
        letter-spacing: -0.02em;
    }
    .kpi-label {
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #94a3b8;
        margin-top: 0.3rem;
    }

    /* ── Fact Card SVG Container ───────────────────────────────── */
    .fact-card-wrap {
        display: flex;
        justify-content: center;
        margin: 1rem 0;
    }

    /* ── Section Divider ───────────────────────────────────────── */
    .section-divider {
        height: 1px;
        background: rgba(255,255,255,0.05);
        margin: 1.6rem 0;
    }

    /* ── Animation ────────────────────────────────────────────── */
    @keyframes fadeUp {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    .animate-in {
        animation: fadeUp 0.4s ease both;
    }

    .flow-heading {
        display: flex; align-items: center; gap: 10px;
        font-size: 1.05rem; font-weight: 700; color: #f1f5f9;
        margin: 0.6rem 0 0.8rem;
    }
    .flow-heading span {
        display: inline-flex; align-items: center; justify-content: center;
        width: 26px; height: 26px; border-radius: 8px;
        background: rgba(213,43,30,0.16); color: #ff9b92; font-size: 0.8rem;
    }
    .hero-section p, .disclaimer { color: #94a3b8 !important; -webkit-text-fill-color: #94a3b8 !important; }
    @media (max-width: 640px) {
        .block-container { padding: 1rem !important; }
        .topbar-meta { display: none; }
        .hero-section h1 { font-size: 1.6rem; }
        .hero-section p { font-size: 0.95rem; }
        .result-placeholder, .verdict-container { padding: 1.2rem; }
    }
</style>
""", unsafe_allow_html=True)


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
        verdict_text = "GESTÜTZT · ENTAILMENT"
        verdict_icon = "✓"
        bg_glow = "rgba(16, 185, 129, 0.15)"
    elif label == 1:
        verdict_color = "#f59e0b"
        verdict_text = "NICHT GEKLÄRT · NEUTRAL"
        verdict_icon = "?"
        bg_glow = "rgba(245, 158, 11, 0.15)"
    else:
        verdict_color = "#ef4444"
        verdict_text = "WIDERSPRUCH · CONTRADICTION"
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

# Wording describes the relationship to the booklet, never a verdict on truth (OST ethics guideline)
VERDICTS = {
    0: ("entailment", "✓", "Vom Abstimmungsbüchlein gestützt",
        "Das Büchlein enthält Aussagen, die diese Behauptung stützen."),
    1: ("neutral", "?", "Im Abstimmungsbüchlein nicht geklärt",
        "Im geprüften Kontext wurde keine Aussage gefunden, die diese Behauptung bestätigt oder widerlegt."),
    2: ("contradiction", "✗", "Widerspricht dem Abstimmungsbüchlein",
        "Das Büchlein enthält Aussagen, die dieser Behauptung widersprechen."),
}

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
    st.session_state.pop("uploaded_pdf", None)  # examples always use their official source
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
AGENT_MARKS = {"done": "✓", "ask": "?", "error": "✕", "running": "…"}
AGENT_BAD_UPLOAD = "Dieses PDF kann nicht gelesen werden. Lade eine andere Datei hoch oder entferne sie."
AGENT_UNKNOWN_SOURCE = ("Der Agent kann die gewählte Quelle nicht prüfen. "
                        "Wähle eine andere Quelle oder lass den Agenten die Vorlage suchen.")


def _drop_agent_check() -> None:
    for key in AGENT_KEYS:
        st.session_state.pop(key, None)


def _vote_date(iso_date: str) -> str:
    return date_label(next(iter(date_options({"vote": {"date": iso_date}}))), UI_LANGUAGE)


def _new_agent_check(own_source: bool, pdf_path: Path, vote: Optional[str], upload_language: Optional[str] = None):
    """A new AgentCheck for the tab's selection, or None after a shown message. With `own_source` the chosen
    booklet (and vote) is the fixed vote; a selection the agent's index does not hold is a translated message.
    With `upload_language` the stored upload at `pdf_path` is the fixed vote: the session registers it itself; an
    unreadable file is a translated message, never the library's text."""
    try:
        with st.spinner(t("Der Agent wird vorbereitet …")):
            fixed_vote_id = None
            if upload_language is not None:
                from src.agent.uploads import upload_vote  # the agent layer is imported on the agent path only
                try:
                    upload = upload_vote(pdf_path, upload_language)
                except ValueError:
                    st.error(t(AGENT_BAD_UPLOAD))
                    return None
                return agent_app.AgentCheck(
                    lambda fixed, on_event: agent_app.make_agent_session(fixed, on_event, upload=upload),
                    upload.vote_id)
            if own_source:
                votes = agent_app.agent_votes()
                try:
                    fixed_vote_id = agent_ui.vote_for_selection(votes, pdf_path.stem, vote).vote_id
                except ValueError:
                    st.error(t(AGENT_UNKNOWN_SOURCE))
                    return None
            return agent_app.AgentCheck(agent_app.make_agent_session, fixed_vote_id)
    except Exception as exc:  # no API key, no index: the check cannot start
        st.error(t('Die Prüfung konnte nicht abgeschlossen werden ({v0}).').format(v0=exc))
        return None


def _step_line(row) -> str:
    return f"{AGENT_MARKS[row.mark]} {t(row.title)}" + (f" · {row.detail}" if row.detail else "")


def _draw_agent_steps(placeholder, check, events, *, question=None) -> Optional[str]:
    """Draws the finished step list into `placeholder`, an error row red. With an open `question` (a `Pending`)
    its text and one button per option sit inside the last ask step. Returns the vote id of a clicked option."""
    rows = agent_ui.step_rows(events)
    asks = [index for index, row in enumerate(rows) if row.mark == "ask"]
    question_at = asks[-1] if asks else len(rows) - 1
    chosen = None
    with placeholder.container():
        st.markdown("#### " + t("Schritte der Prüfung"))
        for index, row in enumerate(rows):
            if row.mark == "error":
                st.error(f"{_step_line(row)}\n\n{row.error}")
            else:
                st.markdown(f'<div class="agent-step agent-step-{row.mark}">{html.escape(_step_line(row))}</div>',
                            unsafe_allow_html=True)
            if question is not None and index == question_at:
                st.markdown(f"**{t(agent_ui.TITLE_QUESTION)}** {t(agent_ui.TEXT_QUESTION)}")
                for option in question.options:
                    if st.button(f"{option.title} · {_vote_date(option.date)}", width="stretch",
                                 key=f"agent_option_{check.key}_{option.vote_id}"):
                        chosen = option.vote_id
    return chosen


def _draw_live_steps(placeholder, events) -> None:
    """The step list while the check runs, as one element: a container redrawn with fewer children keeps the
    surplus ones of its previous drawing, a single element is replaced whole."""
    lines = "".join(f'<div class="agent-step agent-step-{row.mark}">{html.escape(_step_line(row))}'
                    + (f'<br>{html.escape(row.error)}' if row.error else "") + "</div>"
                    for row in agent_ui.step_rows(events, running=True))
    placeholder.markdown(f'<div class="agent-steps-live"><h4>{html.escape(t("Schritte der Prüfung"))}</h4>{lines}</div>',
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
    if kind != "turn" or agent_app.is_question(turn) or turn.prediction is None:
        return
    if agent_ui.outcome_view(turn).kind != "verdict":
        return
    vote = turn.vote
    source = (config.BOOKLETS_DIR / f"{vote.source_id}.pdf", vote.date or None, vote.language, vote.title)  # an upload: no date
    prediction = agent_ui.prediction_for_card(turn)
    st.session_state["last_check"] = {"signature": st.session_state["agent_signature"], "result": prediction,
                                      "notice": None, "created_at": datetime.now(timezone.utc).isoformat(),
                                      "source": source, "discarded": agent_app.is_discarded(turn)}
    if save_history:
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
        st.error(t('Die Prüfung konnte nicht abgeschlossen werden ({v0}).').format(v0=turn))
        return
    if agent_app.is_question(turn):
        return
    view = agent_ui.outcome_view(turn)
    if view.kind != "verdict":
        show = {"ok": st.success, "warn": st.warning, "error": st.error, "info": st.info}[view.tone]
        show(f"**{t(view.title)}** · {t(view.text)}")


def run_agent_check(steps, verify_clicked: bool, claim: str, claim_lang: str, signature: tuple,
                    own_source: bool, pdf_path: Path, vote: Optional[str], save_history: bool,
                    upload_language: Optional[str] = None) -> None:
    """The fact-check tab on the agent: starts a check on «Behauptung prüfen», shows its steps live in `steps`, the
    question inside its step and continues the same check with the clicked option. Draws into the current column.
    With `upload_language` the PDF at `pdf_path` is an own upload in that language and the fixed source."""
    if verify_clicked and claim.strip():
        st.session_state.pop("batch_check", None)
        st.session_state.pop("last_check", None)
        _drop_agent_check()
        check = _new_agent_check(own_source, pdf_path, vote, upload_language)
        if check is not None:
            st.session_state.update(agent_session=check, agent_signature=signature, agent_turn=None, agent_events=[])
            _run_agent(steps, check, lambda session: session.start(claim, claim_lang))
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
                               question=turn if agent_app.is_question(turn) else None)
    if chosen is not None and check.session.pending is not None and not check.running:
        _run_agent(steps, check, lambda session: session.answer(chosen))
        _adopt_agent_turn(check, save_history)
        _draw_agent_steps(steps, check, st.session_state["agent_events"])
        turn = st.session_state.get("agent_turn")
    _show_agent_outcome(turn)




# =============================================================================
# UI LAYOUT
# =============================================================================

# Ambient glow effect
st.markdown('<div class="ambient-glow"></div>', unsafe_allow_html=True)

# ── Top Bar ──────────────────────────────────────────────────────────────────
st.markdown(th("""
<div class="topbar">
    <div class="topbar-brand">
        <div class="logo-mark">+</div>
        <span class="brand-text">Fact Attack</span>
    </div>
    <div class="topbar-meta">
        <span class="topbar-tag">Offizielle Quellen · DE / FR / IT</span>
    </div>
</div>
"""), unsafe_allow_html=True)

st.selectbox("Sprache / Langue / Lingua", list(LANGUAGES), format_func=LANGUAGES.get, key="ui_language")
st.caption(t("Die Sprache der Oberfläche ändert weder die Behauptung noch die Quelle. Forschungsnotizen, Beispieltitel und Exporte behalten ihre Originalsprache."))

# ── Hero Section ─────────────────────────────────────────────────────────────
st.markdown(th("""
<div class="hero-section">
    <h1>Was steht wirklich im Büchlein?</h1>
    <p>Prüfe eine politische Behauptung am offiziellen Abstimmungsbüchlein. Mit Belegstelle zum Nachlesen.</p>
</div>
"""), unsafe_allow_html=True)


# ── Navigation Tabs ──────────────────────────────────────────────────────────
HISTORY_PATH = config.DATA_DIR / ".local-history/checks.sqlite3"
tab_factcheck, tab_history, tab_benchmark, tab_methodology = st.tabs([
    t("Faktencheck"),
    t("Prüfverlauf"),
    t("Ergebnisse & Vergleiche"),
    t("So funktioniert’s"),
])


# =============================================================================
# TAB 1: INTERAKTIVER FAKTENCHECK
# =============================================================================
with tab_factcheck:
    if not BOOKLET_CATALOG:
        st.warning(t("Noch keine offiziellen Büchlein installiert. Bitte im Projektordner `python -m src.booklet_catalog` ausführen."))
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
    uploaded_file = st.session_state.get("uploaded_pdf")
    agent_mode = agent_on
    own_upload = agent_mode and uploaded_file is not None  # an own PDF is always the check's fixed source
    own_source = own_upload or (agent_mode and bool(st.session_state.get("agent_own_source")))
    col_in, col_out = st.columns([1, 1.12], gap="large")

    # ── LEFT: input ──────────────────────────────────────────────────────────
    with col_in:
        st.markdown(th('<div class="flow-heading"><span>1</span> Was möchtest du prüfen?</div>'), unsafe_allow_html=True)
        claim = st.text_area(
            t("Behauptung"), key="claim_input", height=130,
            placeholder=t("Zum Beispiel: Die Vorlage erhöht das Rentenalter auf 67 Jahre."),
            label_visibility="collapsed",
        )
        with st.expander(t("Keine eigene Behauptung? Beispiel auswählen"), expanded=False):
            st.caption(t("Ein Beispiel füllt Behauptung und Quelle aus. Die Prüfung startest du anschliessend selbst."))
            for case in DEMO_CASES:
                if f"{case['booklet_date']}_{case['booklet_language']}.pdf" not in AVAILABLE_SOURCES:
                    continue
                st.button(case["title"], key=f"story_{case['id']}", width="stretch",
                          on_click=_apply_case, args=(case,))
        active_case = next((c for c in DEMO_CASES if c["id"] == st.session_state.get("active_case")
                            and c["claim"] == claim), None)
        if active_case:
            st.caption(active_case["story"])

        if agent_mode:  # the agent finds the source itself; choosing it is optional
            source_box = st.expander(t(agent_ui.TITLE_PICK_SOURCE), expanded=own_source or getattr(
                st.session_state.get("agent_turn"), "outcome", None) == "quelle_fehlt")
        else:
            st.markdown(th('<div class="flow-heading"><span>2</span> Quelle wählen</div>'), unsafe_allow_html=True)
            source_box = st.container()
        with source_box:
            if own_upload:
                st.caption(t("Das eigene PDF ist die Quelle der Prüfung; der Agent sucht nur darin."))
            elif agent_mode:
                st.checkbox(t("Diese Quelle für die Prüfung vorgeben"), key="agent_own_source",
                            help=t("Ohne Häkchen sucht der Agent die passende Vorlage selbst."))
            c_search, c_year = st.columns([2, 1])
            with c_search:
                source_query = st.text_input(t("Thema oder Vorlage suchen"), key="source_query",
                                            placeholder=t("z. B. Mietrecht, Rente, biodiversité"),
                                            help=t("Sucht lokal in den Vorlagentiteln auf DE, FR und IT; keine Volltextsuche im PDF."))
            with c_year:
                years = ["Alle Jahre"] + sorted({entry["date"][:4] for entry in AVAILABLE_SOURCES.values()}, reverse=True)
                if st.session_state.get("source_year", "Alle Jahre") not in years:
                    st.session_state["source_year"] = "Alle Jahre"
                source_year = st.selectbox(t("Jahr"), years, key="source_year", format_func=t)
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
                selected_ballot_name = st.selectbox(t("Abstimmung"), list(filtered_dates), key="ballot", disabled=no_source_matches or uploaded_file is not None, format_func=lambda label: date_label(label, UI_LANGUAGE))
                selected_date = BOOKLET_CATALOG[selected_ballot_name]
            with c_lang:
                language_options = ["DE", "FR", "IT"] if uploaded_file is not None else languages_for_date(AVAILABLE_SOURCES, selected_date)
                if st.session_state.get("booklet_lang") not in language_options:
                    st.session_state["booklet_lang"] = language_options[0]
                selected_lang = st.radio(t("Sprache des Büchleins"), language_options, horizontal=True, key="booklet_lang")
                lang_code = selected_lang.lower()

            source_date = None if uploaded_file is not None else selected_date
            pdf_path = config.BOOKLETS_DIR / f"{selected_date}_{lang_code}.pdf"
            if uploaded_file is not None:
                uploaded_bytes = bytes(uploaded_file.getbuffer())
                pdf_path = store_uploaded_source(config.BOOKLETS_DIR, uploaded_bytes)
                st.success(t('✓ {v0} geladen').format(v0=uploaded_file.name))
                st.info(t("Aktive Quelle: eigenes PDF. Das Abstimmungsdatum ist unbekannt; die offizielle Datumsauswahl ist deaktiviert."))

            vote_options = [VOTE_AUTO] + ([] if uploaded_file is not None else DEMO_VOTES.get(pdf_path.name, SOURCE_METADATA.get(pdf_path.name, {}).get("votes", [])))
            if st.session_state.get("vote") not in vote_options:
                st.session_state["vote"] = VOTE_AUTO
            selected_vote = st.selectbox(t("Vorlage"), vote_options, key="vote", format_func=lambda label: t(label) if label == VOTE_AUTO else label)
            vote = None if selected_vote == VOTE_AUTO else selected_vote
            if uploaded_file is None:
                st.caption(t('{v0} Abstimmungstermine · {v1} Sprachfassungen lokal verfügbar').format(v0=len(BOOKLET_CATALOG), v1=len(AVAILABLE_SOURCES)))
                st.link_button(t("Original bei der Bundeskanzlei öffnen ↗"), SOURCE_METADATA[pdf_path.name]["url"])

        with st.expander(t("Sprache der Behauptung"), expanded=False):
            claim_lang_choice = st.selectbox(t("Erkennung"), ["Automatisch", "DE", "FR", "IT"], key="claim_lang", format_func=t)
            st.caption(t("Deutsch, Französisch und Italienisch werden automatisch erkannt. Die Quelle darf eine andere Sprache haben."))
        claim_lang = guess_language(claim) if claim_lang_choice == "Automatisch" else claim_lang_choice.lower()
        save_history = st.checkbox(t("Prüfung lokal im Verlauf speichern"), value=True, key="save_history",
                                   help=t("Speichert Behauptung, Quelle und Ergebnis auf diesem Rechner. Der Verlauf ist für Nutzer dieser lokalen App-Installation sichtbar."))
        verify_clicked = st.button(t("Behauptung prüfen"), type="primary", width="stretch", disabled=not claim.strip() or (
            not agent_mode and no_source_matches and uploaded_file is None))
        st.caption(t("Beim Prüfen werden die Behauptung und ausgewählte Textstellen an Apertus auf CSCS gesendet."))
        if not agent_on:
            st.caption(t("Die Agentenschicht ist nicht verfügbar; die Prüfung läuft wie bisher ohne Agenten."))
        st.markdown(
            th('<div class="disclaimer">Vergleicht die Aussage mit dem Büchlein. Keine allgemeine Wahrheitsprüfung und keine Abstimmungsempfehlung.</div>'),
            unsafe_allow_html=True,
        )

        with st.expander(t("Weitere Optionen: Suche & eigenes PDF"), expanded=False):
            experiments = st.checkbox(t("Vektor-Experimente aktivieren"), key="enable_vector_rag")
            options = strategy_options(experiments)
            if st.session_state.get("strategy_option") not in options:
                st.session_state["strategy_option"] = STANDARD
            st.radio(t("Kontext für Apertus"), options, key="strategy_option", format_func=t)
            strat = strategy_from_label(st.session_state["strategy_option"])
            if strat in ("vector", "vector_hybrid"):
                st.caption(t("Experimentelle Suche. Im bisherigen Dev-Vergleich war die Standardstrategie besser."))
                if not rag_available():
                    st.info(t("Die lokale Vektorsuche ist in dieser Installation noch nicht verfügbar."))
            st.file_uploader(t("Eigenes PDF laden"), type=["pdf"], key="uploaded_pdf",
                help=t("Die Datei wird lokal gespeichert; beim Prüfen werden ausgewählte Textstellen an Apertus gesendet."))

        with st.expander(t("Mehrere Aussagen aus einem Text prüfen"), expanded=False):
            batch_text = st.text_area(t("Text mit mehreren Aussagen"), key="batch_text", height=140, max_chars=20000)
            st.caption(t("Lokale Satzvorschläge, keine automatische Faktenauswahl. Bitte Text und Auswahl prüfen; Abkürzungen können falsch getrennt werden."))
            candidates = suggest_statements(batch_text)
            if len(candidates) > 30:
                st.info(t("Bitte kürze den Text auf höchstens 30 Satzvorschläge."))
                candidates = []
            if st.session_state.get("batch_selected"):
                st.session_state["batch_selected"] = [value for value in st.session_state["batch_selected"] if value in candidates]
            selected_claims = st.multiselect(t("Aussagen auswählen"), candidates, key="batch_selected", max_selections=MAX_CHECKS)
            st.caption(t("{n} Einzelprüfungen ausgewählt (maximal 5). Jede kann mehrere API-Anfragen auslösen. Alle verwenden die oben gewählte Quelle und Vorlage.").format(n=len(selected_claims)))
            batch_clicked = st.button(t("Ausgewählte Aussagen prüfen"), key="run_batch",
                                      disabled=not selected_claims or (no_source_matches and uploaded_file is None))
        batch_signature = (tuple(selected_claims), str(pdf_path),
                           hashlib.sha256(pdf_path.read_bytes()).hexdigest() if pdf_path.exists() else None,
                           vote, strat, claim_lang_choice)

        previous_batch = st.session_state.get("batch_check")
        retry_count = sum(bool(item["error"]) for item in previous_batch["outcomes"]) if previous_batch and previous_batch["signature"] == batch_signature else 0
        retry_clicked = False
        if retry_count:
            st.caption(t("Nur {n} fehlgeschlagene Aussagen werden erneut geprüft; erfolgreiche Antworten bleiben erhalten.").format(n=retry_count))
            retry_clicked = st.button(t("Nur fehlgeschlagene Aussagen erneut prüfen"), key="retry_batch")

    if batch_clicked or retry_clicked:
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

    # ── RIGHT: result ────────────────────────────────────────────────────────
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
                'Hier erscheint dein Ergebnis mit der passenden Belegstelle. Gib eine Behauptung ein, wähle die Quelle und starte die Prüfung.'}</p>
                <div class="outcome-row"><span class="outcome-dot dot-entailment"></span><strong>Gestützt</strong> — passende Aussage gefunden</div>
                <div class="outcome-row"><span class="outcome-dot dot-neutral"></span><strong>Nicht geklärt</strong> — im geprüften Kontext offen</div>
                <div class="outcome-row"><span class="outcome-dot dot-contradiction"></span><strong>Widerspruch</strong> — das Büchlein sagt etwas anderes</div>
                <div class="disclaimer">Bei gestützten oder widersprochenen Aussagen kannst du die Belegstelle direkt nachlesen.</div>
            </div>
            """), unsafe_allow_html=True)
        elif result is not None and not current_batch:
            if result.strategy in ("vector", "vector_hybrid"):
                st.caption("Experimentelles Ergebnis · " + ("Vektorsuche" if result.strategy == "vector" else "BM25 + Vektor"))
            if last_check.get("notice"):
                st.markdown(f'<div class="notice-box">ℹ️ {t(last_check["notice"])}</div>', unsafe_allow_html=True)
            # an agent result names its own booklet and vote; the old flow's is the selection
            card_pdf, card_date, card_lang, card_vote = last_check.get("source") or (pdf_path, source_date, lang_code, vote)
            discarded = last_check.get("discarded", False)  # its probabilities and number check belong to a dropped label
            if "source" in last_check:
                st.caption(t("Geprüfte Vorlage: {title}").format(title=t(card_vote)))  # an upload's title is a text

            b_data = engine._get_booklet_data(card_pdf)
            total_pages = len(b_data["pages"])
            pages_read = len(result.context_pages) if result.context_pages else total_pages
            v_cls, v_icon, v_title, v_desc = VERDICTS[result.label]
            v_title, v_desc = t(v_title), t(v_desc)

            def fmt(n: int) -> str:
                return f"{n:,}".replace(",", "'")

            st.markdown(f"""
            <div class="verdict-container verdict-{v_cls} animate-in">
                <div class="verdict-label verdict-label-{v_cls}">
                    <span class="verdict-icon icon-{v_cls}">{v_icon}</span>
                    {t("Abgleich mit dem offiziellen Büchlein")}
                </div>
                <h3 class="verdict-title">{v_title}</h3>
                <p class="verdict-reasoning">{v_desc}</p>
            </div>
            """, unsafe_allow_html=True)

            report_record = {"result": result.model_dump(mode="json"), "source_file": card_pdf.name,
                             "source_sha256": hashlib.sha256(card_pdf.read_bytes()).hexdigest(),
                             "date": card_date, "language": card_lang, "vote": card_vote,
                             "created_at": last_check.get("created_at"),
                             "origin": "saved_demo" if last_check.get("notice") else "live"}
            st.download_button(t("Prüfbericht herunterladen (HTML)"),
                               data=render_check_report([_report_source(report_record)], UI_LANGUAGE),
                               file_name="faktencheck.html", mime="text/html", key="single_report")
            st.caption(t("Druckfähige Datei ohne externe Inhalte. Im Browser öffnen und bei Bedarf als PDF drucken."))

            if getattr(result, "numerical_conflict", None) and not discarded:
                st.markdown(f"""
                <div class="guardrail-banner guardrail-vac animate-in">
                    <span class="guardrail-icon">🔢</span>
                    <div class="guardrail-text"><strong>Hinweis Zahlenabgleich:</strong> {html.escape(result.numerical_conflict)}</div>
                </div>
                """, unsafe_allow_html=True)

            pages_by_no = {p["page_number"]: p for p in b_data["pages"]}
            section_by_page = {p["page_number"]: p.get("section") for p in b_data["paragraphs"]}
            primary_quote, primary_page = "", None

            def evidence_html(src, snippet: str) -> str:
                tags = f'<span class="evidence-tag tag-page">Seite {src.page_number}</span>' if src.page_number else ""
                section = SECTION_DE.get(section_by_page.get(src.page_number))
                if section:
                    tags += f'<span class="evidence-tag tag-section">{section}</span>'
                return (f'<div class="evidence-block animate-in"><div class="evidence-header">{tags}</div>'
                        f'<p class="evidence-quote">«{html.escape(snippet)}»</p></div>')

            if result.label != 1 and result.evidence_sources:
                st.markdown('<div class="claim-stage-label">Belegstellen im Abstimmungsbüchlein</div>', unsafe_allow_html=True)
                snippets = []
                for src in result.evidence_sources:
                    page_obj = pages_by_no.get(src.page_number)
                    snippets.append(best_snippet(src.quote, claim))
                first = result.evidence_sources[0]
                primary_quote, primary_page = snippets[0], first.page_number
                st.markdown(evidence_html(first, snippets[0]), unsafe_allow_html=True)
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
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="width: {result.p_entail*100:.1f}%; background: #10b981;"></div></div>
                            <span class="conf-value">{result.p_entail*100:.0f}%</span>
                        </div>
                        <div class="conf-row">
                            <span class="conf-label">Neutral</span>
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="width: {result.p_neutral*100:.1f}%; background: #f59e0b;"></div></div>
                            <span class="conf-value">{result.p_neutral*100:.0f}%</span>
                        </div>
                        <div class="conf-row">
                            <span class="conf-label">Contradiction</span>
                            <div class="conf-bar-track"><div class="conf-bar-fill" style="width: {result.p_contra*100:.1f}%; background: #ef4444;"></div></div>
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


with tab_history:
    st.markdown(t("### Dein Prüfverlauf"))
    st.caption(t("Lokal auf diesem Rechner gespeichert · Öffnen löst keine neue Prüfung aus. Sichtbar für Nutzer dieser App-Installation."))
    history_view = st.radio(t("Ansicht"), ["Aktive Prüfungen", "Papierkorb"], horizontal=True, key="history_view", format_func=t)
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
        history_query = st.text_input(t("Text in Prüfungen suchen"), key="history_query",
                                     help=t("Sucht lokal in Behauptungen, Vorlagentiteln und gespeicherten Belegzitaten."))
        history_label = st.selectbox(t("Ergebnis filtern"),
                                    ["Alle Ergebnisse", "Gestützt", "Nicht geklärt", "Widerspruch"],
                                    key="history_label", format_func=t)
        history_origin = st.selectbox(t("Antwortart"), ["Alle Antwortarten", "Liveantworten", "Demoantworten"],
                                      key="history_origin", format_func=t)
        history_order = st.selectbox(t("Sortierung"), ["Neueste zuerst", "Älteste zuerst", "Behauptung A–Z"],
                                     key="history_order", format_func=t)
        c_start, c_end = st.columns(2)
        with c_start:
            history_start = st.date_input(t("Prüfdatum ab (UTC)"), value=None, key="history_start")
        with c_end:
            history_end = st.date_input(t("Prüfdatum bis (UTC)"), value=None, key="history_end")
        if history_start and history_end and history_start > history_end:
            st.warning(t("Das Startdatum liegt nach dem Enddatum. Bitte korrigiere den Zeitraum."))
        labels = {"Alle Ergebnisse": None, "Gestützt": 0, "Nicht geklärt": 1, "Widerspruch": 2}
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
            key="history_selected")
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
            st.text(t("Vorlage") + ": " + record["vote"])
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
# TAB 2: BENCHMARK DASHBOARD
# =============================================================================
with tab_benchmark:
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
        return f'<div class="kpi-card"><div class="kpi-value" style="color: {color};">{value}</div><div class="kpi-label">{label}</div></div>'

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
        kpis.append(_kpi(f"{head_adv['macro_f1']:.3f}", f"Macro-F1 Advanced · n={head_adv['n']}", "#34d399"))
    if head_beg:
        kpis.append(_kpi(f"{head_beg['macro_f1']:.3f}", f"Macro-F1 Beginner · n={head_beg['n']}", "#60a5fa"))
    if head_adv:
        kpis.append(_kpi(f"{head_adv['latency_p95_ms'] / 1000:.1f} s", "p95 Antwortzeit (Advanced)", "#fbbf24"))
    full_run = next((run for name, run, ours in comparison if name == "Ganzes Büchlein"), None)
    ours_run = next((run for name, run, ours in comparison if ours), None)
    if full_run and ours_run:
        kpis.append(_kpi(f"−{(1 - ours_run['avg_prompt_tokens'] / full_run['avg_prompt_tokens']) * 100:.0f}%",
                         "Input-Tokens vs. ganzes Büchlein", "#a78bfa"))
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
                    f'<div class="mx-cell" style="background: rgba(57,135,229,{alpha:.2f})" title="{tip}">'
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
            eval_dataset = st.selectbox(t("Datensatz"), list(datasets))
        with c_task:
            eval_task = st.selectbox(t("Task"), ["advanced", "beginner"])
        with c_strat:
            eval_strat = st.selectbox(t("Strategie"), ["hybrid", "retrieval", "full"])
        with c_lim:
            eval_lim = st.selectbox(t("Limit"), [6, 15, 30, "Alle"], index=0)

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
                        "✓": "✅" if r["correct"] else "❌",
                        "Tokens": r["tokens_prompt"] + r["tokens_completion"],
                        "Latenz": f"{r['latency_ms']:.0f} ms",
                    }
                    for r in live_report["results"]
                ],
                width="stretch",
                hide_index=True,
            )


# =============================================================================
# TAB 3: ARCHITEKTUR
# =============================================================================
with tab_methodology:
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

# ── Footer ───────────────────────────────────────────────────────────────────
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
st.caption(t("Hack Apertus 2026 — Team Fact Attack (Track 2A: OST) · Josip Jukic & Felipe Wüthrich · [Unser Entwicklungsbranch](https://github.com/SheylaSam/fact-attack-genai/tree/genai/vector-rag) · [Originalprojekt](https://github.com/JJukic/Apertus-Team-Fact-Attack-2026)"))
