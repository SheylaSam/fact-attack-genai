# Neue Oberfläche (hell/dunkel, Seitenleiste, Schritte in Klartext) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Streamlit-App bekommt einen ruhigen hellen und dunklen Look mit Schalter, eine Seitenleiste statt Tabs, einen Faktencheck in einer Spalte mit Schrittkarten in Klartext und einheitliche Begriffe, ohne Agent, Sitzung oder B0 zu verändern.

**Architecture:** Farben und CSS ziehen aus `app.py` in `src/ui_theme.py` und `src/ui_theme.css` um; der Modus kommt aus `st.session_state["ui_dark"]`. `agent_ui.step_rows` liefert zusätzlich `kind` und `params`, aus denen `agent_ui.row_text` übersetzbare Klartext-Sätze baut; der Graph legt dafür zwei Zahlen in `data` der Ereignisse `verify` und `check_evidence` ab. Die vier Tabs werden zu Seiten, die ein `st.radio` in `st.sidebar` wählt.

**Tech Stack:** Python 3.12, Streamlit 1.65.0 (`AppTest` für UI-Tests), Font Awesome 5.15.4 per CDN, Lato (Google Fonts).

**Spec:** `docs/superpowers/specs/2026-11-15-ui-redesign-design.md` (nur lokal, nicht im Repo).

## Global Constraints

- Agent, `AgentSession`, Werkzeuge, Prompts, Schwellen, B0, Wettbewerbs-CLI und Testset bleiben im Verhalten unverändert. Einzige Änderung im Agenten: zusätzliche Felder in `data` von `verify` und `check_evidence` (Task 2).
- Kein Emoji in der Oberfläche; Icons nur aus Font Awesome 5.15.4 (`https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css`), ohne farbige Kacheln.
- Jedes Urteil und jeder Zustand zeigt Icon **und** Wort; Farbe trägt nie allein Bedeutung.
- Fließtext hält Kontrast ≥ 4.5:1 auf seinem Hintergrund, in beiden Modi.
- Begriffe: Urteile **gestützt**, **widersprochen**, **im Dokument nicht belegt** (kurz „nicht belegt“); Zustände **Rückfrage**, **Quelle fehlt**, **technischer Fehler**. Überschriften als Satz („Vom Büchlein gestützt“, „Widerspricht dem Büchlein“, „Im Büchlein nicht belegt“), Filter und Legenden als Wort.
- Jeder neue sichtbare Text steht in `src/ui_i18n.py` mit FR und IT, gleiche Platzhalter.
- Datenformate bleiben: OST-Format, CSV/JSON-Export, gespeicherte Labels 0/1/2.
- Echte Apertus-Anfragen nur im Rahmen von Sheylas OK vom 15.11.: höchstens 30 insgesamt, 5 verbraucht; gezählt über `.agent-traces/*.requests.jsonl`. Alle Tests laufen mit Mock.
- Vor jedem Commit: `git grep -n -i -f .git/info/forbidden-words` gibt nichts aus.
- Tests: `.venv312/bin/python -m pytest track_2a/tests -q` (aus dem Repo-Wurzelordner) bleibt grün; bestehende Tests werden angepasst, nie gelöscht.

## Review Focus

- Live-Schrittliste mit vielen Ereignissen: bleibt **ein** Markdown-Element, keine übrig gebliebene „läuft“-Karte (Task 3).
- Vorlagentitel oder Agententext mit `<`, `&`, `«»` oder Markdown-Zeichen: wird in Karten als Text gezeigt, nie als HTML (Task 3).
- Ereignis ohne die neuen `data`-Felder (alte Spuren, Mock-Ereignisse): Karte fällt auf den bisherigen Titel plus Zusammenfassung zurück, kein Absturz (Task 2).
- Offene Rückfrage, dann Sprache wechseln, Modus wechseln oder Seite wechseln und zurück: Frage und Knöpfe bleiben, Klick setzt denselben Lauf fort (Tasks 1, 5).
- Klick auf einen Eintrag unter „Zuletzt geprüft“ bei gesetzten Verlaufsfiltern oder Papierkorb-Ansicht: öffnet genau diesen Eintrag (Task 5).

---

### Task 1: Farbschema und Hell/Dunkel-Schalter

**Files:**
- Create: `track_2a/src/ui_theme.py`, `track_2a/src/ui_theme.css`
- Modify: `track_2a/app.py:72-1117` (Style-Block ersetzen), `track_2a/app.py:1621` (Glow entfernen), `track_2a/.streamlit/config.toml`
- Test: `track_2a/tests/test_ui_theme.py`

**Interfaces:**
- Produces: `ui_theme.PALETTES: Dict[str, Dict[str, str]]` mit Schlüsseln `"light"`, `"dark"`; `ui_theme.contrast(fg: str, bg: str) -> float`; `ui_theme.theme_css(mode: str) -> str` (vollständiger `<link>`+`<style>`-Block); Session-Schlüssel `ui_dark: bool` (Standard `False`); CSS-Klassen für spätere Tasks: `.fa-card`, `.step-card`, `.tone-blue|green|yellow|red|grey|orange`, `.verdict-card`, `.pill`, `.quote`.

- [ ] **Step 1: Write the failing tests**

```python
from src import ui_theme

TOKENS = {"bg", "surface", "surface2", "border", "text", "muted", "blue", "green", "yellow", "red", "grey", "orange"}

def test_both_palettes_have_the_same_tokens():
    assert set(ui_theme.PALETTES["light"]) == set(ui_theme.PALETTES["dark"]) == TOKENS

def test_palette_values_from_the_spec():
    assert ui_theme.PALETTES["light"]["bg"] == "#F3F4F8" and ui_theme.PALETTES["dark"]["bg"] == "#121419"
    assert ui_theme.PALETTES["light"]["blue"] == "#3B62C6" and ui_theme.PALETTES["dark"]["green"] == "#2FD1AE"

def test_text_contrast_in_both_modes():
    for p in ui_theme.PALETTES.values():
        for fg in ("text", "muted"):
            for bg in ("bg", "surface", "surface2"):
                assert ui_theme.contrast(p[fg], p[bg]) >= 4.5

def test_css_declares_every_token_and_links_font_awesome():
    css = ui_theme.theme_css("dark")
    assert all(f"--{name}:" in css for name in TOKENS)
    assert "font-awesome/5.15.4/css/all.min.css" in css and "ambient-glow" not in css
```

In `tests/test_ui_streamlit.py` ergänzen: `test_dark_toggle_switches_the_palette` – App starten, `app.toggle(key="ui_dark").set_value(True).run()`, dann enthält ein Markdown-Element `#121419`, vorher `#F3F4F8`; kein `app.exception`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_ui_theme.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'src.ui_theme'`

- [ ] **Step 3: Implement**

`PALETTES` mit den Werten aus der Tabelle der Spec (light/dark), dazu `orange` `#C25E00` hell / `#FF9440` dunkel und `surface2` `#F7F8FB` / `#232731`; falls ein Wert den Kontrasttest nicht schafft, `muted` abdunkeln bzw. aufhellen, nie den Test lockern. `contrast` nach WCAG 2.x (relative Luminanz). `theme_css(mode)` = FA-Link + Lato-Link + `<style>:root{--bg:…}` + Inhalt von `ui_theme.css`; die CSS-Datei nutzt nur `var(--…)` und überschreibt die Streamlit-Widgets (Hintergrund, Textfeld, Knöpfe primär blau, Aufklapper, Tabs, Radio, Seitenleiste). Altes CSS aus `app.py` übernehmen, soweit Klassen weiter benutzt werden; ungenutzte Regeln und `ambient-glow` streichen. In `app.py` den Style-Block durch `st.markdown(ui_theme.theme_css("dark" if st.session_state.get("ui_dark") else "light"), unsafe_allow_html=True)` ersetzen und in `st.sidebar` `st.toggle(t("Dunkler Modus"), key="ui_dark")` setzen. `config.toml`: `base = "light"`, `primaryColor = "#3B62C6"`, `backgroundColor = "#F3F4F8"`, `secondaryBackgroundColor = "#FFFFFF"`, `textColor = "#1A2233"`. i18n: „Dunkler Modus|Mode sombre|Modalità scura“.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS (alle)

- [ ] **Step 5: Handprobe**

App wie in `docs/genai/UI_UND_PYTHON312.md` starten (mit `HF_HOME=/Users/sheyla/Projects/model-cache`), beide Modi ansehen, Screenshot je Modus nach `.playwright-mcp/`. Abweichende Canvas-Tabellen/Diagramme im Dunkelmodus notieren, wenn lesbar akzeptieren (Spec).

- [ ] **Step 6: Commit**

```bash
git grep -n -i -f .git/info/forbidden-words || true   # muss leer sein
git add track_2a/src/ui_theme.py track_2a/src/ui_theme.css track_2a/app.py track_2a/.streamlit/config.toml track_2a/src/ui_i18n.py track_2a/tests/test_ui_theme.py track_2a/tests/test_ui_streamlit.py
git commit -m "feat(ui): light and dark palette with a sidebar switch"
```

---

### Task 2: Schritte in Klartext

**Files:**
- Modify: `track_2a/src/agent/graph.py:575-606` (nur `data=` ergänzen), `track_2a/src/agent_ui.py:27-167`, `track_2a/src/ui_i18n.py`
- Test: `track_2a/tests/test_agent_ui.py`, `track_2a/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: Spurereignisse wie im Probelauf: `suche_vorlagen`/`act` `data.candidates[]` (`title`, `date`); `ask` `args.antwort`, `data.options[]` (`vote_id`, `title`, `date`); `suche_passagen`/`act` `data.found`; `suche_passagen_vektor`/`act` `data.new`.
- Produces:
  - `verify`-Ereignis: `data={"label": int, "quotes": int}`; `check_evidence`-Ereignis: `data={"valid": int, "quotes": int}` (bei „kein Zitat zu prüfen“ `quotes=0`).
  - `StepRow` bekommt `kind: str = ""` und `params: Tuple[Tuple[str, str], ...] = ()` (hashbar, frozen bleibt); `title` ist jetzt die deutsche Satzvorlage.
  - `agent_ui.row_text(row: StepRow, translate: Callable[[str], str], format_date: Callable[[str], str]) -> str`: übersetzt `title`, übersetzt jeden Parameterwert, der in `TEXT_KEYS` steht, formatiert den Parameter `date` mit `format_date`, setzt alles ein.
  - `agent_ui.STEP_ICONS: Dict[str, Tuple[str, str]]` kind → (FA-Klasse, Ton) für Task 3.
  - `agent_ui.OUTCOME_WORDS = {0: "gestützt", 1: "nicht belegt", 2: "widersprochen"}` (in `TEXT_KEYS`; Task 4 nutzt sie für Filter und Legende).

Satzvorlagen (genau so, neu in `TEXT_KEYS` und `ui_i18n.py`):

| kind | Bedingung | title | params |
|---|---|---|---|
| `votes` | n ≥ 2 | `{n} passende Vorlagen gefunden` | n |
| `votes` | n = 1 | `Passende Vorlage gefunden: {v0}` | v0 |
| `votes` | n = 0 | `TITLE_NO_SOURCE` | – |
| `ask` | offen | `TITLE_QUESTION` | – |
| `ask` | beantwortet | `Du hast gewählt: {v0}, Abstimmung vom {date}` | v0, date |
| `statement` | – | `TITLE_STATEMENT` | – |
| `passages` | – | `{n} Textstellen im Büchlein gefunden` | n |
| `vector` | – | `Nachsuche: {n} neue Textstellen` | n |
| `verify` | – | `Apertus hat geprüft: {v0}` | v0 = `gestützt`/`widersprochen`/`nicht belegt` |
| `evidence` | valid = quotes > 0 | `Alle {n} Zitate stehen wörtlich im PDF` | n |
| `evidence` | 0 < valid < quotes | `{v0} von {n} Zitaten stehen im PDF` | v0, n |
| `evidence` | quotes = 0 | `Kein Zitat zu prüfen` | – |
| `evidence` | valid = 0 < quotes | `Urteil verworfen: kein Zitat steht im PDF` | – |
| `source_missing` | – | `TITLE_NO_SOURCE` | – |

Fehlen die nötigen Daten, bleibt `kind=""` und der bisherige Titel aus `TOOL_TITLES`/`NODE_TITLES`. `detail` bleibt die unveränderte Zusammenfassung des Agenten.

- [ ] **Step 1: Write the failing tests** in `tests/test_agent_ui.py`

```python
def test_vote_search_with_two_candidates_reads_as_a_count(): ...
    # events: decide+act suche_vorlagen mit data.candidates (2 Einträge)
    # row.kind == "votes"; row_text(row, str, str) == "2 passende Vorlagen gefunden"
def test_answered_question_names_title_and_formatted_date(): ...
    # ask mit args.antwort="2025-09-28_de#2", data.options mit title/date
    # row_text(row, str, lambda d: "28. September 2025") == "Du hast gewählt: E-ID-Gesetz, Abstimmung vom 28. September 2025"
def test_verify_and_evidence_use_the_new_data_fields(): ...
    # verify data {"label": 0, "quotes": 5} -> "Apertus hat geprüft: gestützt"
    # check_evidence data {"valid": 5, "quotes": 5} -> "Alle 5 Zitate stehen wörtlich im PDF"
def test_events_without_data_keep_the_old_title_and_summary(): ...
    # act suche_passagen mit data={} -> kind == "", title == "Passagen suchen", detail == summary
def test_row_text_translates_the_outcome_word():
    # translate = lambda s: {"Apertus hat geprüft: {v0}": "Apertus a vérifié : {v0}", "gestützt": "étayée"}.get(s, s)
    # -> "Apertus a vérifié : étayée"
def test_no_vote_id_or_score_reaches_a_title(): ...
    # für den Probelauf-Ablauf: kein row_text enthält "_de#", "Score", "Pool", "Label "
```

In `tests/test_agent_graph.py`: `verify`-Ereignis hat `data == {"label": 0, "quotes": n}`, `check_evidence` `data["valid"]` und `data["quotes"]` (vorhandene Mock-Fixtures nutzen).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_agent_ui.py track_2a/tests/test_agent_graph.py -q`
Expected: FAIL (`row_text` fehlt, `data` leer)

- [ ] **Step 3: Implement** – `data=` in den `emit`-Aufrufen von `graph.py` ergänzen; in `step_rows` je Gruppe `kind`/`params` aus den Ereignisdaten setzen (Tabelle oben), `row_text` und `STEP_ICONS` (`votes` `fa-search` blue, `ask`/`statement` `fa-question` yellow, `passages` `fa-file-alt` blue, `vector` `fa-layer-group` blue, `verify` `fa-balance-scale` blue, `evidence` `fa-check-double` green, `source_missing` `fa-search-minus` grey, Fehler `fa-exclamation-triangle` orange, läuft `fa-circle-notch` grey). Alle neuen Vorlagen in `ui_i18n.py` mit FR/IT.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS; bestehende `step_rows`-Tests, die alte Titel erwarten, auf die neuen Sätze umstellen.

- [ ] **Step 5: Commit**

```bash
git add track_2a/src/agent/graph.py track_2a/src/agent_ui.py track_2a/src/ui_i18n.py track_2a/tests/test_agent_ui.py track_2a/tests/test_agent_graph.py
git commit -m "feat(ui): plain-language step titles from the trace data"
```

---

### Task 3: Schrittkarten zeichnen

**Files:**
- Modify: `track_2a/app.py:1383` (`AGENT_MARKS` entfällt), `track_2a/app.py:1462-1516` (`_step_line`, `_draw_agent_steps`, `_draw_live_steps`)
- Test: `track_2a/tests/test_ui_agent_streamlit.py`

**Interfaces:**
- Consumes: `agent_ui.step_rows`, `row_text`, `STEP_ICONS` (Task 2); CSS-Klassen `.step-card`, `.tone-*` (Task 1).
- Produces: `_step_card(row) -> str` in `app.py`: HTML einer Karte, beginnt mit `<div class="agent-step agent-step-{mark} step-card" data-kind="{kind}">` (die Testhilfe `step_texts` sucht weiter `<div class="agent-step `), enthält `<i class="fas {icon} tone-{ton}" aria-hidden="true">`, den escapten Satz, bei `detail` ein `<details><summary>{t("Details")}</summary>…</details>` mit escaptem Agententext, bei Fehler den escapten Fehlertext.

- [ ] **Step 1: Tests anpassen und ergänzen** in `tests/test_ui_agent_streamlit.py`
  - Bestehende Titelprüfungen (`'Vorlagen suchen'`, `'Passagen suchen'`, `'Prüfung starten'`, `'Belege prüfen'`, `'Rückfrage'`) auf `data-kind` umstellen, z. B. `'data-kind="votes"' in steps` und `'data-kind="evidence"' in steps`.
  - `test_step_cards_escape_titles_and_agent_text`: Fake-Vorlagentitel `"<b>Gesetz</b> & «Test»"` → Karte enthält `&lt;b&gt;Gesetz&lt;/b&gt; &amp;` und kein `<b>Gesetz`.
  - `test_live_step_list_is_one_element`: während `_run_agent` (Fake mit drei Ereignissen) gibt es genau ein Markdown-Element mit `class="agent-steps-live"`, danach keines.
  - `test_cards_use_icons_not_marks`: kein Schritttext enthält `✓`, `✕` oder `…` als Zeichen am Anfang; jede Karte enthält `class="fas `.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_ui_agent_streamlit.py -q`
Expected: FAIL (alte Markup-Form)

- [ ] **Step 3: Implement** `_step_card` und beide Zeichenfunktionen damit; die Live-Liste bleibt ein einziges `placeholder.markdown`. Überschrift „Schritte der Prüfung“ wird „So prüft der Agent“ (i18n FR „Comment l’agent vérifie“, IT „Come verifica l’agente“). Rückfrage-Knöpfe bleiben `st.button` an derselben Stelle (nach der `ask`-Karte); Beschriftung `{Titel} · {Datum}` bleibt.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add track_2a/app.py track_2a/src/ui_i18n.py track_2a/tests/test_ui_agent_streamlit.py
git commit -m "feat(ui): agent steps as cards with icons and collapsible details"
```

---

### Task 4: Urteilskarte, einheitliche Begriffe, keine Emojis

**Files:**
- Modify: `track_2a/app.py` (`VERDICTS`, Legende `:1960-1972`, Urteilskarte `:1994-2003`, Hinweisboxen mit `ℹ️`/`🔢`, Verlaufsfilter `:2130-2143`, Teilen-Karte), `track_2a/src/agent_ui.py:46-51`, `track_2a/src/pdf_preview.py`, `track_2a/src/check_report.py`, `track_2a/src/ui_i18n.py`
- Test: `track_2a/tests/test_ui_streamlit.py`, `track_2a/tests/test_ui_agent_streamlit.py`, `track_2a/tests/test_ui_i18n.py`

**Interfaces:**
- Produces: `VERDICTS[label] = (css_class, fa_icon, titel, text)` mit `0: ("supported", "fa-check-circle", "Vom Büchlein gestützt", …)`, `1: ("not-proven", "fa-minus-circle", "Im Büchlein nicht belegt", …)`, `2: ("contradicted", "fa-times-circle", "Widerspricht dem Büchlein", …)`; Legende und Filter nutzen `agent_ui.OUTCOME_WORDS` (Task 2). Verlaufsfilter-Optionen `["Alle Ergebnisse", "gestützt", "nicht belegt", "widersprochen"]`.

- [ ] **Step 1: Write the failing tests**
  - `test_ui_streamlit.py`: Filter `app.selectbox(key='history_label').set_value('widersprochen')` statt `'Widerspruch'`; neuer Test `test_no_old_terms_or_emoji_in_the_rendered_page`: nach einer Prüfung mit Mock enthält kein Markdown/Caption-Text „Nicht geklärt“, „Widerspruch“ (als Wort) oder ein Zeichen aus `\U0001F300-\U0001FAFF`, `ℹ`, `✅`.
  - `test_ui_agent_streamlit.py`: `has_verdict` sucht `class="verdict-card`; Urteilskarte enthält `fa-check-circle` und „Vom Büchlein gestützt“.
  - `test_ui_i18n.py`: jedes neue deutsche Wort hat FR und IT.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_ui_streamlit.py track_2a/tests/test_ui_agent_streamlit.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** – Urteilskarte im großen, ruhigen Stil (Icon + Satz + Text, darunter Zitat als `.quote`, Pills `.pill` für Seite, Abschnitt, „{n} weitere Belege“); vorhandene Extras (Download, Seitenvorschau, Details, Wahrscheinlichkeiten) bleiben darunter. Alle Emojis in sichtbaren Texten durch FA-Icons ersetzen. Gespeicherte Datensätze und Exporte nicht anfassen; nur ihre Anzeige. `TITLE_NOT_PROVEN` = „Im Büchlein nicht belegt“.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add track_2a/app.py track_2a/src/agent_ui.py track_2a/src/pdf_preview.py track_2a/src/check_report.py track_2a/src/ui_i18n.py track_2a/tests
git commit -m "feat(ui): one set of verdict terms, verdict card with icons, no emoji"
```

---

### Task 5: Seitenleiste mit Navigation und „Zuletzt geprüft“

**Files:**
- Modify: `track_2a/app.py:1623-1660` (Kopf, Sprachwahl, `st.tabs`), `with tab_…:`-Blöcke bei `:1663`, `:2113`, und die beiden übrigen
- Test: `track_2a/tests/test_ui_streamlit.py`, `track_2a/tests/test_ui_agent_streamlit.py`

**Interfaces:**
- Consumes: `load_checks(HISTORY_PATH)`, `_reset_history_filters`, Session-Schlüssel `history_view`, `history_selected` (bestehend).
- Produces: `PAGES = ("Faktencheck", "Prüfverlauf", "Ergebnisse & Vergleiche", "So funktioniert’s")`; `st.sidebar` in dieser Reihenfolge: Marke „Fact Attack“, `st.radio(key="nav", options=PAGES, format_func=t, label_visibility="collapsed")`, `st.expander(t("Zuletzt geprüft"), expanded=False)` mit bis zu 5 `st.button` (`key=f"recent_{record['id']}"`, `on_click=_open_saved, args=(record['id'],)`), `st.selectbox(key="ui_language")` (bisheriger Schlüssel, Label versteckt), `st.toggle(key="ui_dark")` (Task 1). `_open_saved(check_id: str) -> None` setzt `nav="Prüfverlauf"`, ruft `_reset_history_filters()`, setzt `history_view="Aktive Prüfungen"` und `history_selected=check_id`. `_short_claim(text: str) -> str` = `textwrap.shorten(text, width=42, placeholder=" …")`.

- [ ] **Step 1: Write the failing tests**
  - Testhilfe `goto(app, page)` = `app.radio(key="nav").set_value(page).run()`; alle Tests, die Verlaufs-/Ergebnis-Widgets ansprechen, rufen sie vorher auf.
  - `test_sidebar_has_navigation_language_and_dark_switch`: `app.sidebar` enthält Radio `nav`, Selectbox `ui_language`, Toggle `ui_dark`; es gibt kein `st.tabs` mehr auf oberster Ebene.
  - `test_recent_checks_are_collapsed_and_open_the_saved_result(isolated_history)`: zwei gespeicherte Prüfungen, Expander „Zuletzt geprüft“ zu; Klick auf `recent_<id der älteren>` bei gesetztem `history_query="xyz"` → `app.radio(key="nav").value == "Prüfverlauf"` und `app.selectbox(key="history_selected").value == id`; Beschriftung ≤ 42 Zeichen + Icon-Wort.
  - `test_recent_section_is_absent_without_history`.
  - `test_open_question_survives_a_page_switch`: Rückfrage offen, `goto("Prüfverlauf")`, `goto("Faktencheck")` → Knöpfe da, Klick setzt fort (`fakes.answers`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_ui_streamlit.py track_2a/tests/test_ui_agent_streamlit.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** – `st.tabs` durch `page = st.session_state.get("nav", PAGES[0])` ersetzen, jedes `with tab_x:` durch `if page == PAGES[i]:` (gleiche Einrückung). Prüfen, ob eine Seite Namen nutzt, die nur in einer anderen definiert werden (z. B. `pdf_path`, `vote`); solche Definitionen vor die Seitenwahl ziehen. Die Urteils-Icons der Liste aus `VERDICTS` (Task 4), mit Wort im `help`-Text oder in der Beschriftung. Kopfzeile mit Glow und Sprach-Selectbox oben entfällt; Hinweis zur Oberflächensprache als `help` an der Selectbox.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add track_2a/app.py track_2a/src/ui_i18n.py track_2a/tests
git commit -m "feat(ui): sidebar navigation with recent checks instead of tabs"
```

---

### Task 6: Faktencheck in einer Spalte, „Weitere Möglichkeiten“, Fußzeile

**Files:**
- Modify: `track_2a/app.py:1687-1972` (Spalten, Aufklapper, Hinweise), `track_2a/app.py:2496` (Fußzeile), `track_2a/src/ui_i18n.py`
- Test: `track_2a/tests/test_ui_agent_streamlit.py`, `track_2a/tests/test_ui_streamlit.py`

**Interfaces:**
- Consumes: alle bestehenden Widget-Schlüssel (`claim_input`, `ballot`, `vote`, `source_year`, `agent_own_source`, `save_history`, Upload-, Beispiel- und Mehrfach-Schlüssel) bleiben gleich.
- Produces: Im Agentenmodus eine Spalte: Kopfkarte (Titel „Was steht wirklich im Büchlein?“, Untertitel), `claim_input`, Knopf „Behauptung prüfen“, eine Hinweiszeile `<i class="fas fa-lock">` + „Behauptung und Textstellen gehen an Apertus (CSCS). Kein allgemeiner Wahrheitscheck, keine Abstimmungsempfehlung.“; darunter `st.expander(t("Weitere Möglichkeiten"), expanded=own_source or picked_source)` mit `st.tabs` [„Quelle selbst wählen“, „Beispiel auswählen“, „Eigenes PDF & Suche“, „Mehrere Aussagen“, „Sprache der Behauptung“] und der Checkbox `save_history`; darunter Schrittkarten und Urteil. Ohne Agent bleibt der Zwei-Spalten-Aufbau mit Pflicht-Quellenwahl. Fußzeile: „Fact Attack · GenAI-Modulprojekt · Team Fact Attack · [Repository](https://github.com/SheylaSam/fact-attack-genai) · [Originalprojekt](https://github.com/JJukic/Apertus-Team-Fact-Attack-2026)“.

- [ ] **Step 1: Write the failing tests**
  - `test_agent_page_is_one_column_with_one_more_options_expander`: im Agentenmodus genau ein Expander auf oberster Ebene der Seite mit Label „Weitere Möglichkeiten“; die alten fünf Expander-Labels gibt es nicht mehr als Expander; alle bisherigen Widget-Schlüssel sind auffindbar.
  - `test_more_options_opens_itself_when_the_source_must_be_picked`: französische Behauptung ohne Quelle → Expander „Weitere Möglichkeiten“ `expanded`.
  - `test_footer_links_main_not_the_old_branch`: Fußzeile enthält `github.com/SheylaSam/fact-attack-genai)` und nicht `tree/genai/vector-rag`.
  - Bestehende Tests, die Expander per Label öffnen, auf die Tabs umstellen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv312/bin/python -m pytest track_2a/tests/test_ui_agent_streamlit.py track_2a/tests/test_ui_streamlit.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** – Streamlit erlaubt keine Expander in Expandern: die inneren Aufklapper der fünf Bereiche werden in ihren Tabs zu `st.container()` mit `st.caption`-Überschrift. Reihenfolge der Inhalte innerhalb jedes Bereichs unverändert.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m pytest track_2a/tests -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add track_2a/app.py track_2a/src/ui_i18n.py track_2a/tests
git commit -m "feat(ui): one-column fact check, one place for more options, current footer"
```

---

### Task 7: Echter Lauf, Screenshots, Doku

**Files:**
- Modify: `docs/genai/UI_UND_PYTHON312.md` (Abschnitt „Oberfläche ab 15.11.2026“: Seitenleiste, Hell/Dunkel, Startbefehl mit `HF_HOME=/Users/sheyla/Projects/model-cache`), `README.md` (ein Satz)
- Create: `docs/genai/assets/ui-hell.png`, `docs/genai/assets/ui-dunkel.png`

- [ ] **Step 1:** Anfragestand zählen: `cat .agent-traces/*.requests.jsonl | wc -l` → ≤ 30 − 12 = 18, sonst Sheyla fragen.
- [ ] **Step 2:** App starten, Behauptung „Mit dem E-ID-Gesetz stellt der Bund die E-ID selbst aus.“ prüfen, Rückfrage mit „28. September 2025“ beantworten. Erwartet: Rückfrage-Karte mit beiden E-ID-Gesetzen, danach Urteil „Vom Büchlein gestützt“ mit Zitat und Seite. Screenshots hell und dunkel (Modus umschalten, kein zweiter Lauf).
- [ ] **Step 3:** Anfragestand erneut zählen und in der Doku nennen (verbraucht / 30).
- [ ] **Step 4:** Doku ergänzen; `git grep -n -i -f .git/info/forbidden-words` leer.
- [ ] **Step 5: Commit**

```bash
git add docs/genai/UI_UND_PYTHON312.md README.md docs/genai/assets/ui-hell.png docs/genai/assets/ui-dunkel.png
git commit -m "docs(ui): new interface, screenshots of the demo claim"
```
