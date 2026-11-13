# Agentenschicht Stufe 4: Oberfläche B, Unterbrechen/Fortsetzen, Sitzungs-Memory – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Faktencheck-Tab prüft über den Agenten: ein Eingabefeld, eine live gefüllte Schrittliste, die Rückfrage als Knöpfe im betroffenen Schritt. Die Antwort setzt denselben Lauf fort (`interrupt`), die zuletzt gewählte Vorlage erscheint bei der nächsten Rückfrage als zusätzlicher Knopf.

**Architecture:** `ask` ruft im interaktiven Modus (Graph mit Checkpointer) `interrupt` auf; ohne Checkpointer bleibt alles wie in Stufe 3 (CLI und Stufe-5-Evaluation brauchen `rueckfrage` als Ergebnis). Eine `AgentSession` hält Graph, Checkpointer, `AgentTools`, Budget und Tracer über Streamlit-Reruns hinweg. Der Lauf geht in einen Worker-Thread, ein `on_event`-Hook des `Tracer` füllt eine Queue, das Skript zeichnet die Schrittliste. Die Darstellung steckt in einem reinen, testbaren Modul `src/agent_ui.py`; `app.py` bekommt nur Verdrahtung.

**Tech Stack:** Python 3.12, LangGraph 1.2.11 (`MemorySaver`, `interrupt`, `Command`), Streamlit 1.65.0 (`AppTest`), bestehendes `src/ui_i18n.py` (DE/FR/IT), unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-agentenschicht-design.md`, Abschnitte „Von Sheyla entschieden“ (Oberfläche B, Platz in der App), „Oberfläche, Variante B“, „Memory (SW8)“, „Stufen“ (Stufe 4, Ziel Fr 13.11.2026). Grundlage: `docs/genai/AGENT_STUFE3.md` (Befunde, offene Punkte).

**Branch:** `genai/agent-stufe4` (von `origin/genai/vector-rag` @ `73a204a`, Upstream bewusst entfernt). PRs gehen auf `genai/vector-rag`, nie auf `main`.

**Nicht in Stufe 4:**
- Safeguarding inklusive zusammengesetzter Behauptungen, Evaluation B0/B1/B2, Embedding-Vergleich, Schwellen des Wächters (Stufe 5)
- Automatische Quellenfindung auf Französisch/Italienisch
- MCP in der Oberfläche (die UI nutzt lokale Werkzeuge; `McpBackend` bleibt CLI, das `cache_resource`-Muster aus `AGENT_STUFE3.md` ist der Weg, falls nötig)
- jeder Apertus-Aufruf: Tests und Handprobe laufen mit Mock

**Reihenfolge:** 1 → 2 → 3 → 4 sind abhängig. 5 (Uploads) und 6 (Memory) hängen nur von 4 ab. 6 ist kürzbar (Kürzungsreihenfolge Punkt 2, dann entfällt der „wie zuletzt“-Knopf). 7 zuletzt.

## Entscheidungen dieses Plans (bitte prüfen)

| Frage | Entscheidung | Grund |
|---|---|---|
| Wann `interrupt`? | Nur mit Checkpointer (`build_graph(..., checkpointer=...)`); `run_check` bleibt unverändert | `interrupt` ohne Checkpointer wirft; CLI/Evaluation messen `outcome == "rueckfrage"` |
| Serialisierung | `MemorySaver` mit `JsonPlusSerializer(allowed_msgpack_modules=...)` für die State-Typen | Spike (offline): Rundlauf klappt, LangGraph warnt aber „wird künftig blockiert“ |
| `ask` läuft nach Resume von vorn | Event und Seiteneffekte stehen **nach** `interrupt(...)` | Spike: `ask` lief zweimal; ein Event davor käme doppelt |
| Nach der Antwort | Vorlage wird fest (`AgentTools.fix_vote`), Suchschritte beginnen neu, Pool, Guard-Cache, Budget und Tracer laufen weiter; der Entscheider bekommt die Antwort als Beobachtung (`decider.observation_messages`), der Systemprompt die Zeile der festen Vorlage | wie Stufe 3 bei der Nachsuche: keine zwei User-Nachrichten hintereinander |
| Live-Schrittliste | Lauf im Worker-Thread, `Tracer.on_event` → `queue.Queue`, Zeichnen nur im Skript-Thread | `st.*` aus Fremd-Threads braucht `ScriptRunContext`; die Queue vermeidet das |
| Werkzeuge in der UI | lokal (`LocalBackend`), ein `SourceIndex` per `st.cache_resource` | der lokale Qdrant-Index erlaubt einen Prozess |
| Ergebniskarte | bestehende Karte, gespeist mit einer `PredictionResult`-Kopie: Label aus `outcome`, Belege = die geprüften `result.evidence` | die Karte liest `label`, `evidence_sources`, `context_pages` |
| Verlauf, Mehrfachprüfung | wie heute; der Verlauf speichert nur Urteile (`gestuetzt`, `widersprochen`, `nicht_belegt`) | Spec: bleiben |
| Uploads | `custom_<sha256>#0` als Vorlage der Sitzung; Vektor-Nachsuche liefert für Uploads keine Passagen (kein Index) | Spec: Katalog-IDs oder Upload-IDs, nie freie Pfade |
| Test-/Handprobe-Modus | Umgebungsvariable `FACT_ATTACK_AGENT_MOCK=1`: `ApertusClient(mock=True)` und `ScriptedDecider` | kein Netz, keine Anfragen |

## Global Constraints

- B0 unverändert: `src/inference.py`, `src/apertus_client.py`, `src/http_chat.py`, Wettbewerbs-CLI, `requirements.txt`.
- `track_2a/data/genai/testset/` wird nie angefasst; Testfälle nur über `load_testset("dev")`.
- Schwellen des Wächters bleiben 0.01 / 0.0 / None (`settings.py`); `TOOL_MODE` bleibt `json`.
- Keine Apertus-Anfrage ohne Sheylas ausdrückliches OK mit Höchstzahl; echte Läufe nie wiederholen.
- Neue UI-Texte: deutscher Text als Schlüssel in `src/ui_i18n.py`, mit FR und IT, Platzhalter in allen drei Sprachen gleich, kein `|` im Text.
- Code-Kommentare englisch, Texte deutsch, Tests unittest-Stil. Eingabelänge 1 bis 1000 Zeichen (`settings.MAX_CLAIM_CHARS`).
- Schlüsseldatei aus `FACT_ATTACK_ENV_FILE` nie ausgeben. `docs/genai/assets/langsmith-trace-mock-2026-11-01.png` nie stagen.
- Suite: `cd track_2a && HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 ../.venv312/bin/python -m pytest -q`; Stand vor Stufe 4: 859 passed, 8 bekannte Warnungen. `AppTest` mit `default_timeout=50`.

## Review Focus

1. Seite neu laden oder Behauptung/Quelle ändern, während eine Rückfrage offen ist: alter Lauf wird verworfen, keine Knöpfe der alten Frage, kein Resume auf den alten Thread (Task 4).
2. Doppelklick oder zweite Antwort auf eine schon beantwortete Rückfrage: genau eine Fortsetzung, keine zusätzliche Anfrage (Task 2).
3. Antwort mit einer Vorlage, die nicht zur Frage gehört (verfälschter Knopfwert): abgelehnt, die Rückfrage bleibt offen (Task 2).
4. Budget erschöpft oder Apertus-Ausfall nach der Antwort: roter technischer Fehler im betroffenen Schritt, nie ein Urteil (Task 2, 4).
5. Französische Behauptung ohne Quelle: sichtbare Bitte „Quelle selbst wählen“ statt leerem Bereich; Sprachwechsel der Oberfläche bei offener Rückfrage übersetzt die Knöpfe, ohne den Lauf zu verlieren (Task 3, 4).

---

### Task 1: Interaktiver Graph (`interrupt`) und feste Vorlage nach der Antwort

**Files:**
- Modify: `track_2a/src/agent/graph.py` (`ask`, `build_graph`, `run_check`), `track_2a/src/agent/tools.py` (`fix_vote`)
- Test: `track_2a/tests/test_agent_graph.py`, `track_2a/tests/test_agent_tools.py`

**Interfaces:**
- Produces: `AgentTools.fix_vote(vote_id: str) -> VoteRef`; `graph.make_checkpointer() -> MemorySaver`; `graph.prepare(claim: str, tools: AgentTools, tracer: Tracer, claim_language: Optional[str] = None) -> Union[AgentResult, dict]` (der Early-Exit-Ergebnis oder der Startzustand `{"claim","language","messages","history","steps","search_steps"}`); `build_graph(..., checkpointer: Optional[BaseCheckpointSaver] = None)`.
- Consumes: Stufe-3-`build_graph`, `_Nodes.ask`, `Decider.observation_messages`.

- [ ] **Step 1: Failing tests** (`test_agent_tools.py`): `test_fix_vote_hides_the_vote_search_and_keeps_the_pool` (nach `fix_vote(V1.vote_id)` fehlt `suche_vorlagen` in `available_specs()`, `check_vote_id(V2.vote_id)` wirft, `passages_for` bleibt); `test_fix_vote_rejects_unknown_ids_and_a_second_vote` (ValueError, deutsche Meldung). (`test_agent_graph.py`): `test_interactive_graph_pauses_at_the_question_and_resumes_with_the_chosen_vote` (Index `CLOSE`, Graph mit `make_checkpointer()`, `thread_id`; erster `invoke` liefert `__interrupt__` mit Frage und `[V1, V2]`, `graph.get_state(cfg).next == ('ask',)`, `Command(resume=V2.vote_id)` führt zu `gestuetzt` auf V2, `tools.fixed_vote_id == V2.vote_id`, `engine.verify_calls[0]['vote'] == V2.title`); `test_resumed_check_shows_exactly_one_ask_event_after_the_answer` (ein `ask`-Event, `args["antwort"] == V2.vote_id`); `test_resume_alternates_roles_in_the_transcript` (kein Paar User/User in den Entscheider-Nachrichten nach der Antwort); `test_checkpointer_serializes_without_unregistered_type_warnings` (`warnings.catch_warnings(record=True)` über Pause und Resume ergibt keine Meldung „unregistered type“); `test_graph_without_checkpointer_still_ends_with_rueckfrage` (bestehende Tests bleiben grün, kein `__interrupt__`); `test_prepare_returns_the_early_results_run_check_gave_before` (Fremdsprache ohne Quelle → `quelle_fehlt`, `legacy`-Policy → `technischer_fehler`).
- [ ] **Step 2: Run, expect FAIL:** `pytest tests/test_agent_graph.py tests/test_agent_tools.py -q -k "fix_vote or interactive or resume or checkpointer or prepare"`.
- [ ] **Step 3: Implement.** `AgentTools.fix_vote` setzt `fixed_vote_id` einmalig (nur bekannte Vorlage, nur wenn noch keine andere fest ist). `prepare` zieht den Anfang von `run_check` heraus (Länge, Policy, Sprache, Nachrichten); `run_check` ruft `prepare` und bleibt im Verhalten gleich. `make_checkpointer()` baut `MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=[...]))` mit allen Typen, die der State beim `interrupt` trägt (mindestens `VoteRef`, `Observation`, `AgentAction`; die Warnung im Test nennt fehlende). Mit Checkpointer ruft `ask` `interrupt({"question", "options": [ids], "asked_by"})` **als Erstes** auf; danach: Antwort muss eine der Options-IDs sein (sonst `ValueError`, der Knoten meldet technisch), `tools.fix_vote`, `ask`-Event (`summary`: „Rückfrage beantwortet: …“, `args={"optionen": ids, "antwort": id}`), `messages[0]` = `system_prompt(vote)`, Antwort als Beobachtung über `decider.observation_messages(state["action"], Observation("rueckfrage", ANSWER_PROMPT.format(...)))`, `search_steps = 0`, Route `decide`. Ohne Checkpointer unverändert. Die Kante `ask` wird bedingt (`finish` oder `decide`), das Rekursionslimit wächst im interaktiven Modus um einen zweiten Suchlauf.
- [ ] **Step 4: Run, expect PASS** und danach die ganze Suite (859 + neue, 8 Warnungen).
- [ ] **Step 5: Commit** `feat(agent): interactive graph pauses at the question and resumes with the chosen vote`.

### Task 2: `AgentSession` und Live-Hook im `Tracer`

**Files:**
- Create: `track_2a/src/agent/session.py`
- Modify: `track_2a/src/agent/trace.py` (`on_event`), `track_2a/src/agent/graph.py` (kleine Nachbesserung aus dem Review von Task 1, siehe Step 3)
- Test: `track_2a/tests/test_agent_session.py`, `track_2a/tests/test_agent_graph.py` (Tracer)

**Interfaces:**
- Consumes: Task 1 (`prepare`, `build_graph(checkpointer=)`, `make_checkpointer`, `AgentResult`).
- Produces:
  - `Tracer(jsonl_path=None, on_event: Optional[Callable[[TraceEvent], None]] = None)`; `on_event` läuft nach dem Schreiben, jedes Event genau einmal.
  - `@dataclass(frozen=True) class Pending: question: str; options: Tuple[VoteRef, ...]; asked_by: str`; `Turn = Union[Pending, AgentResult]`.
  - `class AgentSession(tools, decider, engine, tracer, *, budget=None, nachsuche=True, max_steps=settings.MAX_STEPS)` mit `start(claim: str, claim_language: Optional[str] = None) -> Turn`, `answer(vote_id: str) -> Turn`, Eigenschaft `pending -> Optional[Pending]`, `events -> List[TraceEvent]`.

- [ ] **Step 1: Failing tests** (`test_agent_session.py`, Fakes aus `test_agent_graph.py`): `test_start_returns_pending_for_a_close_claim_and_a_result_for_a_clear_one`; `test_answer_continues_the_same_check_and_returns_the_result` (gleiche `tools`, gleiches Budget-Objekt, `result.vote == V2`, `result.events` enthält Ereignisse vor und nach der Antwort); `test_second_answer_after_the_result_is_refused_without_new_requests` (`ValueError`, `engine.verify_calls` unverändert); `test_answer_with_a_vote_outside_the_options_is_refused_and_the_question_stays_open` (`pending` bleibt); `test_answer_without_a_question_is_refused`; `test_budget_refusal_after_the_answer_is_a_technical_error` (`RequestBudget` mit Limit, das nach der Antwort erschöpft ist → `technischer_fehler`, kein Urteil); `test_start_twice_discards_the_open_question` (neuer Thread, alte Frage gilt nicht mehr); `test_on_event_sees_every_event_once_in_order`; `test_early_results_come_back_as_results` (Fremdsprache → `quelle_fehlt`). (`Tracer`): `test_on_event_is_called_after_the_line_is_written`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `start` ruft `prepare`; ist das Ergebnis ein `AgentResult`, wird es zurückgegeben. Sonst neuer `thread_id` (uuid), `graph.invoke(state, cfg)`; enthält das Ergebnis `__interrupt__`, wird `Pending` aus dem Payload gebaut (Optionen als `VoteRef` über `tools.check_vote_id`), sonst `_result(...)` (die Ereignisse des Laufs ab `first_event`). `answer` prüft `pending` und die Options-IDs, ruft `invoke(Command(resume=vote_id), cfg)` und löscht `pending` bevor es zurückgibt (zweiter Aufruf: `ValueError`). Ausnahmen außerhalb der Knoten werden wie in `run_check` zu `technischer_fehler`.
- [ ] **Step 3b: Nachbesserung `graph.py` (Review Task 1).** `_result` füllt `question` und `options` nur bei `outcome == "rueckfrage"` (wie `asked_by`); der interaktive `ask` setzt `passage_searches = 0` zurück (die feste Vorlage braucht vor `quelle_fehlt` eine Passagensuche in **ihr**). Die Docstrings von `build_graph` und `AgentSession` sagen, dass `tools`, `engine` und `tracer` über Pause und Resume dieselben Instanzen bleiben müssen (Pool, Guard-Cache und Vorlagenliste liegen nicht im Checkpoint). Tests: `test_resumed_result_carries_no_stale_question_or_options`, `test_quelle_fehlt_after_the_answer_needs_a_passage_search_in_the_chosen_vote`.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(agent): AgentSession keeps one check across question and answer`.

### Task 3: Darstellung als reines Modul (`src/agent_ui.py`) und Übersetzungen

**Files:**
- Create: `track_2a/src/agent_ui.py`
- Modify: `track_2a/src/ui_i18n.py` (neue Zeilen)
- Test: `track_2a/tests/test_agent_ui.py`, `track_2a/tests/test_ui_i18n.py`

**Interfaces:**
- Consumes: `TraceEvent`, `AgentResult`, `Pending` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class StepRow: mark: Literal["done","ask","error","running"]; title: str; detail: str; error: Optional[str]` (deutsche Texte, die UI übersetzt mit `t()`).
  - `step_rows(events: Sequence[TraceEvent], *, running: bool = False) -> List[StepRow]`: fasst `decide`+`act` je Schritt zusammen; eine Wächter-Abfangung **oder** Modell-`rueckfrage` und das zugehörige `ask` ergeben **eine** `ask`-Zeile (kein doppeltes „?“); der abgefangene Schritt zeigt kein ✓.
  - `outcome_view(result: AgentResult) -> OutcomeView` mit `kind: Literal["verdict","state"]`, `title`, `text`, `tone` (`ok|warn|error|info`) für alle sechs Ergebnisse; `quelle_fehlt` wegen Sprache nennt „Quelle selbst wählen“.
  - `prediction_for_card(result: AgentResult) -> PredictionResult`: Kopie von `result.prediction`, Label aus `outcome` (0/1/2), `evidence_sources` aus den geprüften `result.evidence`.
  - `vote_for_selection(votes: Mapping[str, VoteRef], source_id: str, title: Optional[str]) -> VoteRef` (Titel gleich → diese Vorlage, sonst `#0`; ValueError ohne Treffer).
  - `stream(call: Callable[[], Turn], events: "queue.Queue[TraceEvent]") -> Iterator[Union[TraceEvent, Turn]]`: läuft `call` im Worker-Thread, liefert Events der Queue sofort, zuletzt den `Turn`; eine Ausnahme von `call` wird im Aufrufer erneut geworfen.

- [ ] **Step 1: Failing tests** (`test_agent_ui.py`): `test_a_guard_question_is_one_ask_row_and_the_blocked_action_has_no_checkmark`; `test_a_model_question_is_one_ask_row`; `test_errors_make_the_affected_row_red_with_the_message` (Budget, Schrittgrenze, Wächter-Suche); `test_running_adds_an_open_row`; `test_outcome_view_covers_every_outcome` (Unterschleife über alle sechs, `technischer_fehler` zeigt `result.error`); `test_language_quelle_fehlt_asks_to_pick_the_source`; `test_prediction_for_card_maps_outcome_to_label_and_uses_checked_evidence` (auch `nicht_belegt` nach verworfenem Urteil: Label 1, keine Belege); `test_vote_for_selection_by_title_and_whole_booklet`; `test_stream_yields_events_while_the_call_runs_then_the_turn` (Call blockiert auf einem Event, der Test sieht das erste Event vor dem Ende); `test_stream_reraises_the_calls_exception`. (`test_ui_i18n.py`): `test_every_literal_text_of_agent_ui_has_a_translation` (AST über `agent_ui.py`: jede Zeichenkette in `t(...)`/`TEXT_KEYS` hat FR und IT), `test_new_rows_keep_placeholders`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** nach den Signaturen. Texte als Konstanten in `agent_ui.py`, damit der AST-Test sie findet; Übersetzungszeilen in `_ROWS`.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(ui): step rows, outcome view and card input for the agent result`.

### Task 4: Faktencheck-Tab auf den Agenten umstellen

**Files:**
- Modify: `track_2a/app.py` (Faktencheck-Tab ab Zeile ~1421, Eingabe, Schrittliste, Rückfrage, Ergebnis; neue Fabrik `make_agent_session`)
- Test: `track_2a/tests/test_ui_agent_streamlit.py`; bestehende `test_ui_streamlit.py` bleibt grün (einzige erlaubte Änderung dort: die Fixture `isolated_history` setzt `FACT_ATTACK_AGENT=0`, damit die alten Tests den alten Ablauf prüfen)
- Nicht ändern: `requirements.txt` (Wettbewerbs-Lock ohne langgraph/httpx)

**Interfaces:**
- Consumes: Task 2 (`AgentSession`, `Pending`), Task 3 (`step_rows`, `outcome_view`, `prediction_for_card`, `vote_for_selection`, `stream`).
- Produces: `app.make_agent_session(fixed_vote_id: Optional[str], on_event: Callable[[TraceEvent], None]) -> AgentSession` (Tests patchen sie); `app.get_agent_index()` (`st.cache_resource`, ein `SourceIndex`; Freigabe über `on_release`, sonst `atexit`); Session-State-Schlüssel `agent_session`, `agent_turn`, `agent_signature`, `agent_events`.

- [ ] **Step 1: Failing tests** (`AppTest.from_file(app.py, default_timeout=50)`, `app.make_agent_session` auf Fakes gepatcht, `FACT_ATTACK_AGENT_MOCK` unnötig): `test_a_clear_claim_shows_the_steps_and_the_result_card`; `test_a_close_claim_shows_the_question_with_one_button_per_option_in_the_ask_step`; `test_clicking_an_option_continues_the_check_and_shows_the_result`; `test_changing_the_claim_with_an_open_question_discards_it_and_starts_fresh` (Review Focus 1); `test_changing_the_ui_language_keeps_the_open_question_and_translates_the_buttons` (Review Focus 5); `test_french_claim_without_source_asks_to_pick_the_source`; `test_picking_the_source_yourself_runs_with_the_fixed_vote_and_has_no_question_step`; `test_technical_error_is_red_in_its_step_and_there_is_no_verdict` (Review Focus 4); `test_history_saves_verdicts_but_not_questions`; `test_multi_check_and_history_tabs_work_as_before`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `app.py` importiert die Agentenschicht erst in `make_agent_session` bzw. einem geschützten Helfer (`agent_available() -> bool`: `langgraph` und `httpx` importierbar, Indexordner `settings.INDEX_DIR` vorhanden, `FACT_ATTACK_AGENT != "0"`). Ist sie nicht verfügbar, bleibt der Tab beim heutigen Ablauf (`engine.verify_claim`) mit einem kurzen übersetzten Hinweis; zusätzlicher Test `test_without_the_agent_layer_the_tab_keeps_the_old_flow`. `make_agent_session` baut für jede Prüfung neue `AgentTools` (ein beantworteter Lauf lässt die feste Vorlage in den Tools zurück) und `AgentSession.start`/`answer` laufen nie gleichzeitig: ein `agent_running`-Flag im Session-State verhindert eine zweite Anfrage, solange ein Lauf im Worker-Thread offen ist (Doppelklick). Zusätzlicher Test `test_double_click_on_an_option_continues_once`. Bei einem verworfenen Urteil (`result.note` enthält `graph.DISCARDED_NOTE`) blendet die Karte die Wahrscheinlichkeitsbalken (`p_entail`, `p_contra`) und den Zahlenabgleich aus, weil sie dem Label `nicht_belegt` widersprächen (`prediction_for_card` ändert sie nicht); die `ValueError` von `vote_for_selection` zeigt die UI nie roh, sondern als übersetzte allgemeine Meldung. Tests: `test_a_discarded_verdict_card_has_no_probability_bars`, `test_unknown_source_selection_shows_a_translated_message`. Die linke Spalte behält Eingabe und „Quelle selbst wählen“ (aufklappbar); „Behauptung prüfen“ erzeugt über `make_agent_session` eine Sitzung (Signatur aus Behauptung, Quelle, Sprache). Ändert sich die Signatur, werden `agent_session`/`agent_turn` verworfen. Der Lauf geht über `stream(...)`: die Schrittliste in einem `st.empty()` wird je Event neu gezeichnet. Bei `Pending` zeigt die `ask`-Zeile die Frage und `st.button` je Option (Key mit Thread-/Optionskennung); ein Klick ruft `answer` und rendert weiter. Ergebnisse: Urteil → `last_check` mit `prediction_for_card(result)` und der bestehende Kartencode; sonst `outcome_view`. `save_check` nur bei Urteilen. Mehrfachprüfung und Verlauf unangetastet. `make_agent_session` baut `LocalBackend(get_agent_index(), get_engine(), BOOKLETS_DIR)`, `RequestBudget` pro Sitzung, `make_decider(...)`; mit `FACT_ATTACK_AGENT_MOCK=1` Mock-Client und `ScriptedDecider`.
- [ ] **Step 4: Run, expect PASS**, ganze Suite inklusive bestehender UI-Tests.
- [ ] **Step 5: Commit** `feat(ui): fact-check tab runs through the agent with live steps and in-place question`.

### Task 5: Eigene PDF-Uploads als Quellen des Agenten

**Files:**
- Create: `track_2a/src/agent/uploads.py`
- Modify: `track_2a/src/agent/search_backend.py` (`LocalBackend.add_vote`), `track_2a/app.py` (Upload-Pfad: heute nimmt ein hochgeladenes PDF den alten Ablauf, siehe die Beschriftung bei `agent_mode`; künftig den Agenten) und `track_2a/src/agent_app.py` (hier lebt `make_agent_session` seit Task 4; sie bekommt `upload: Optional[VoteRef] = None`, registriert sie über `add_vote` und setzt sie als feste Vorlage; die Tests patchen `src.agent_app.*`, siehe `tests/test_ui_agent_streamlit.py`)
- Test: `track_2a/tests/test_agent_uploads.py`

**Interfaces:**
- Consumes: `store_uploaded_source` (`src/ui_strategies.py`), `LocalBackend`, Task 4.
- Produces: `upload_vote(path: Path, language: str) -> VoteRef` (`custom_<sha>#0`, `title="Eigenes PDF"`, `pages` = alle Seiten; `ValueError` auf Deutsch bei leerer/kaputter Datei); `LocalBackend.add_vote(vote: VoteRef) -> None` (nur `custom_`-Vorlagen, ergänzt `votes`; `vector_passages` für sie liefert `[]`).

- [ ] **Step 1: Failing tests:** `test_upload_vote_is_whole_booklet_with_all_pages`; `test_broken_or_empty_pdf_is_refused_with_a_german_message`; `test_add_vote_accepts_only_custom_ids` (Katalog-ID und Pfad wie `../x` abgelehnt); `test_upload_check_runs_with_the_fixed_vote` (Agentenlauf über Fake-Engine; `retrieve_calls[0]['booklet_pdf']` endet auf `custom_<sha>.pdf`); `test_vector_search_for_an_upload_finds_nothing_and_the_first_result_stands` (Nachsuche-Pfad „ohne neue Passagen“); AppTest: `test_uploaded_pdf_check_has_the_same_steps_and_card`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** nach den Signaturen; `app.py` registriert die Upload-Vorlage in der Sitzung und startet mit `fixed_vote_id`.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(agent): uploaded PDFs are session sources of the agent`.

### Task 6: Sitzungs-Memory „wie zuletzt“ (kürzbar)

**Files:**
- Modify: `track_2a/src/agent/session.py`, `track_2a/src/agent_ui.py`, `track_2a/app.py`, `track_2a/src/ui_i18n.py`
- Test: `track_2a/tests/test_agent_session.py`, `track_2a/tests/test_agent_ui.py`, `track_2a/tests/test_ui_agent_streamlit.py`

**Interfaces:**
- Consumes: Task 2–4.
- Produces: `AgentSession.start(claim, claim_language=None, *, last_vote: Optional[VoteRef] = None)` (ein Hinweis „Zuletzt gewählte Vorlage der Person: «Titel» (id).“ als zusätzliche Zeile in der User-Nachricht, **nie** als feste Vorlage); `agent_ui.options_with_last(options: Sequence[VoteRef], last: Optional[VoteRef]) -> List[OptionButton]` (`OptionButton(vote, label, is_last)`: „wie zuletzt: …“ ist ein zusätzlicher Knopf, steht die letzte Vorlage schon unter den Optionen, trägt dieser Knopf die Markierung statt eines Doppels); Session-State-Schlüssel `last_vote_id`.

- [ ] **Step 1: Failing tests:** `test_last_vote_is_only_a_hint_and_never_fixes_the_vote` (Wächter fragt trotzdem; `tools.fixed_vote_id is None` vor der Antwort); `test_hint_text_names_title_and_id`; `test_options_with_last_adds_one_extra_button_or_marks_the_existing_one`; AppTest: `test_second_question_offers_the_last_chosen_vote_as_extra_button`, `test_last_vote_is_never_chosen_without_a_click`, `test_no_last_vote_without_a_previous_answer`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** nach den Signaturen; nach jeder Antwort schreibt `app.py` `last_vote_id`.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(ui): the last chosen vote is offered as an extra button, never taken silently`.

### Task 7: Doku, Handprobe, offene Punkte

**Files:**
- Create: `docs/genai/AGENT_STUFE4.md`
- Modify: `docs/genai/AGENT_STUFE3.md` (offene Punkte „Stufe 4“ abhaken), `docs/genai/UI_UND_PYTHON312.md` falls Start/Umgebung betroffen
- Log: `.superpowers/sdd/2026-11-01-agentenschicht-stufe3/progress.md` (git-ignoriert)

**Interfaces:** keine neuen.

- [ ] **Step 1: Handprobe mit Mock.** `FACT_ATTACK_AGENT_MOCK=1 ../.venv312/bin/streamlit run app.py` (eigener Port), im Browser: eindeutige Behauptung, mehrdeutige Behauptung mit Knopf, Quelle selbst wählen, Upload, Sprachwechsel DE→FR bei offener Frage. Screenshots der Schrittliste und der Rückfrage in `docs/genai/assets/` (kein `langsmith-trace-mock`-PNG stagen). Der eingebaute Browser verliert `localStorage` beim Neustart; nichts darauf bauen.
- [ ] **Step 2: `AGENT_STUFE4.md` schreiben:** Ablauf mit `interrupt`, Tracer-Hook und Thread/Queue, Memory-Regel, Grenzen (Upload ohne Vektor-Nachsuche, nur lokale Werkzeuge in der UI, Verlauf nur Urteile), die Entscheidungen der Tabelle, offene Punkte für Stufe 5. `AGENT_STUFE3.md`: den zwei-„?“-Hinweis und „Stufe 4“-Punkte als erledigt markieren.
- [ ] **Step 3: Ganze Suite**, Stand und Zahl im Dokument; `git status` zeigt nur erwartete Dateien.
- [ ] **Step 4: Commit** `docs(agent): stage 4, interactive question and live steps`.
- [ ] **Step 5 (nur mit Sheylas OK und Höchstzahl):** höchstens ein echter UI-Lauf mit Apertus; ohne OK bleibt es beim Mock und das Dokument sagt das.
