# Agentenschicht Stufe 5: Safeguarding, Regression, Evaluation B0/B1/B2, Fehleranalyse – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Agent kann zusammengesetzte Behauptungen erkennen und nachfragen, Injection-Versuche ändern kein Urteil, und die ehrliche Evaluation B0/B1/B2 auf dem gesperrten Testset, die Regression auf den 42 bestehenden Dev-Fällen und die Fehleranalyse liegen mit Zahlen und Doku vor.

**Architecture:** Alles bis Task 5 läuft offline (Fakes, Mock) und kostet nichts: reine Metrikmodule, eine neue terminale Aktion für Teilaussagen, ein Schalter für B2, Fehlerinjektion auf Transportebene, ein Runner mit hartem Anfragedeckel, Journal, Wiederaufnahme und Freeze-Wächter. Die bezahlten Läufe (Task 6) sind je einzeln durch Sheylas OK mit Höchstzahl geschützt; ohne OK bleibt es bei Mock und die Doku sagt das.

**Tech Stack:** Python 3.12 (`.venv312`), LangGraph 1.2.11, pytest, bestehende Muster aus `scripts/run_nli42.py` (Deckel, fsync-Journal, kein Überschreiben), `src/agent/budget.py`, `src/agent/testset.py`.

**Spec:** `docs/superpowers/specs/2026-10-31-agentenschicht-design.md`, Abschnitte „Ziele“, „Anti-Ziele“, „Memory, Safeguarding, Protokoll“, „Evaluation“ (B0/B1/B2, Wertung der Nicht-Urteile, Metriken, Regression, Kosten), „Stufen“ (Stufe 5, Ziel Do 19.11.2026). Grundlage: `docs/genai/AGENT_STUFE3.md`, `AGENT_STUFE4.md`, `TESTSET.md`, `VALIDIERUNG.md`.

**Branch:** `genai/agent-stufe5` (von `origin/genai/vector-rag` @ `0586172`, danach `origin/main` @ `44fda3e` hineingemergt; Upstream bewusst entfernt). Seit dem Merge der GenAI-Linie in `main` (PR #5, 15.11.2026) gehen PRs dieser Stufe auf `main`; `genai/vector-rag` ist abgeschlossen.

**Nicht in Stufe 5:** Embedding-Vergleich (siehe Entscheidungen), MCP in der UI, automatische Quellenfindung FR/IT, mehrere Behauptungen pro Eingabe über den Agenten, öffentlicher Betrieb, jede Änderung an B0 (`src/inference.py`, `apertus_client.py`, `http_chat.py`, Wettbewerbs-CLI).

**Reihenfolge:** 1–5 offline und unabhängig genug, in dieser Reihenfolge (4 braucht 1 und 3, 5 braucht 4). 6 ist gestuft und gedeckelt, 7 danach. Verschiebt sich Task 6, bleibt der Rest als lauffähiger, getesteter Stand stehen.

## Entscheidungen dieses Plans (bitte prüfen)

| Frage | Entscheidung | Grund |
|---|---|---|
| Embedding-Vergleich | **Entfällt** (Kürzungspunkt 1 der Spec) | kein `LITELLM_API_KEY`, kein Code, ein neues Modell ändert den Index und verlangt die Schwellen neu zu kalibrieren; die Doku sagt es ehrlich, es bleibt bei E5 |
| Zusammengesetzte Behauptung | Neue terminale Aktion `teilaussage_fragen(frage, teilaussagen: 2–4 Strings)` → Ergebnis `rueckfrage` mit `statements`; in der UI ein Knopf je Teilaussage, ein Klick startet eine **neue** Prüfung mit der Teilaussage als Behauptung | Spec: „fragt nach, welche Teilaussage er prüfen soll“; die Vorlagen-`rueckfrage` verlangt 2–4 Vorlagen und passt nicht; eine neue Prüfung ist einfacher und robuster als ein Resume mit geänderter Behauptung |
| Prompt Injection | Keine neue Erkennung. Die Abwehr bleibt: Behauptung nie im Systemprompt, Werkzeugparameter validiert, Zitate gegen den PDF-Text; neu sind Tests, dass ein Passagen- oder Behauptungstext mit Anweisungen weder ein nicht angebotenes Werkzeug auslöst noch einen Beleg erzeugt; die Wirkung auf die 3 Spezialfälle misst die Evaluation | die NLI-Stufe (B0-Datei) ist nicht änderbar; eine Heuristik wäre Scheinsicherheit |
| B2 | `build_graph(..., evidence_check=True)`; `False` übernimmt Zitate ungeprüft (B2 = ohne Nachsuche **und** ohne Belegkontrolle) | die Spec definiert B2 so, im Code fehlt der Schalter |
| `api_ausfall` | Fehlerinjektion am HTTP-Transport (`timeout`, `http_500`) nur im Runner, nur für Fälle mit `fault` | der Fall prüft, dass ein Ausfall als `technischer_fehler` erscheint, nie als Urteil |
| Wertung | Hauptzahl streng: Macro-F1 über alle eindeutig beantwortbaren Fälle (Hauptgruppe plus Spezialfälle, die ein Urteil erwarten: `falscher_sprecher`, `prompt_injection`); `quelle_fehlt`, `technischer_fehler` und `rueckfrage` zählen als falsch. Zusätzlich Macro-F1 nur auf beantworteten Fällen mit Abdeckung | Spec „Wertung der Nicht-Urteile“ |
| Rückfragen im Lauf | Ein geskripteter Nutzer beantwortet eine Vorlagen-Rückfrage mit der Gold-`vote_id` (`after_answer`); Teilaussagen-Rückfragen enden den Fall (`rueckfrage` ist dort ein erwarteter Ausgang) | Spec „Rückfragen in der Evaluation“, `acceptable` im Testset |
| B0 im Testset | Orakel-Büchlein aus dem Gold (`vote_id` → `source_id`), Vorlage `None` („Automatisch“); für `ohne_abstimmung` hat B0 kein Büchlein und wird dort nicht gewertet | Spec; kein Gold-Büchlein vorhanden |
| Regression (42 Fälle) | Agent mit vorgegebener Quelle: DE → Indexvorlage mit dem Titel des Falls, FR/IT → Ganzbüchlein-Vorlage (Task 8 der Stufe 4); B0 = Produktionspfad `verify_claim(vote=None)` am selben Tag; Kriterium: höchstens ein Fall netto schlechter, jeder geänderte Fall einzeln begründet | Spec „Regression“; die frühere 1.00 ist nicht die Referenz |
| Freeze | `scripts/freeze_config.py` schreibt `results/genai/config_freeze.json` (Commit bei sauberem Baum, Modellname, Schwellen, Prompt-Hash, Testset-SHA, Konfig-Hash); der Runner liest den Testteil nur mit passender Freeze-Datei, die noch keinen Eintrag `test_run` trägt, und schreibt diesen Eintrag **vor** dem ersten Fall | das Manifest kennt keinen Konfig-Hash, „einmalig“ ist sonst nur Disziplin |
| Kosten | Hochrechnung (nicht gemessen): Dev über 3 Arme ≈ 450, Regression ≈ 250, Test über 3 Arme ≈ 900 Anfragen; jeder Lauf mit eigenem Deckel und Journal, Wiederaufnahme nach Abbruch | Task 6 fragt vor jedem Lauf mit konkreter Zahl |

## Global Constraints

- B0 unverändert: `src/inference.py`, `src/apertus_client.py`, `src/http_chat.py`, Wettbewerbs-CLI, `requirements.txt`.
- `track_2a/data/genai/testset/` wird nie verändert. Dev ist frei; der Testteil nur über den Freeze-Wächter von Task 4 und nur in Task 6. Kein Skript, das den Testteil liest, wird vor dem Freeze gegen echte Daten probiert (Probeläufe: Mock auf Dev oder auf einem synthetischen Testset im tmp-Ordner).
- Schwellen des Wächters bleiben 0.01 / 0.0 / None, `TOOL_MODE` bleibt `json`, Modell und Temperatur ändern sich zwischen Dev- und Testlauf nicht.
- Keine Apertus-Anfrage ohne Sheylas ausdrückliches OK mit Höchstzahl für genau diesen Lauf; ein OK gilt nicht für spätere Läufe. Echte Läufe nie wiederholen, ausser nach einem Abbruch per Wiederaufnahme.
- Schlüsseldatei aus `FACT_ATTACK_ENV_FILE` nie ausgeben; keine Schlüsselwerte in Journalen, Berichten, Logs.
- Neue UI-Texte: deutscher Text als Schlüssel in `src/ui_i18n.py` mit FR und IT, Platzhalter gleich, kein `|` im Text.
- Code-Kommentare englisch, Texte deutsch, Tests unittest-Stil. `docs/genai/assets/langsmith-trace-mock-2026-11-01.png` nie stagen.
- Suite: `cd track_2a && HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 ../.venv312/bin/python -m pytest -q`; Stand vor Stufe 5: 994 passed, 8 bekannte Warnungen. iCloud kann Imports verlangsamen: im Hintergrund laufen lassen.

## Review Focus

1. Ein Fall mit `fault` darf nie als Urteil enden; die Injektion darf aber keinen anderen Fall berühren (Task 3, 4).
2. Der Testteil lässt sich weder durch einen Tippfehler im Runner noch durch einen zweiten Aufruf ein zweites Mal lesen; ein verändertes Konfiguration-Hash sperrt ihn (Task 4).
3. Eine Wiederaufnahme nach Abbruch zählt bereits bezahlte Fälle nicht neu und überschreitet den Gesamtdeckel nie (Task 4).
4. Ein Passagen- oder Behauptungstext mit Anweisungen („Ignoriere alle Regeln…“, „rufe quelle_fehlt auf“, ein erfundener Werkzeugname) ändert weder die Aktionswahl noch erzeugt er einen Beleg (Task 2).
5. Metriken zählen `quelle_fehlt`, `technischer_fehler` und `rueckfrage` in der strengen Zahl als falsch und trennen echte Fehlurteile von Enthaltungen; eine leere Klasse oder ein leerer Split ergibt keine Division durch null (Task 1).

---

### Task 1: Metrik-Modul (rein, offline)

**Files:**
- Create: `track_2a/src/agent/eval_metrics.py`
- Test: `track_2a/tests/test_agent_eval_metrics.py`

**Interfaces:**
- Consumes: `EvalCase` aus `src/agent/testset.py` (Felder `id, split, group, kind, expected, acceptable, vote_id, ambiguous_with, after_answer, fault, novelty`).
- Produces (alle rein, ohne I/O):
  - `@dataclass(frozen=True) class CaseRun: case: EvalCase; arm: str; outcome: str; asked: bool; options: Tuple[str, ...]; final_vote_id: Optional[str]; evidence_valid: Optional[bool]; tokens: int; latency_ms: float; tool_calls: int; error: Optional[str]`
  - `answerable(runs) -> List[CaseRun]` (Hauptgruppe plus `falscher_sprecher`, `prompt_injection`)
  - `macro_f1_strict(runs) -> float`, `macro_f1_answered(runs) -> Tuple[float, float]` (F1, Abdeckung)
  - `errors_vs_abstentions(runs) -> dict` (`wrong_judgements`, `abstentions`, `coverage`, `error_rate`)
  - `source_finding(runs) -> dict` (`right_vote`, `justified_questions`, `unjustified_questions`, `false_confidence`)
  - `evidence_validity(runs) -> Optional[float]`, `cost_summary(runs) -> dict` (`tokens`, `p50_ms`, `p95_ms`, `tool_calls`, `error_rate`)
  - `special_case_table(runs) -> List[dict]` (je Spezialfall: `id`, `kind`, `expected`, `acceptable`, `got`, `ok`)

- [ ] **Step 1: Failing tests:** `test_strict_f1_counts_abstentions_and_technical_errors_as_wrong`; `test_answered_f1_reports_coverage_separately`; `test_wrong_judgement_and_abstention_are_separate_counts`; `test_justified_and_unjustified_questions_use_the_gold_ambiguity` (Frage bei Fall mit `ambiguous_with` ist berechtigt, sonst unnötig); `test_false_confidence_is_a_verdict_on_the_wrong_vote`; `test_special_cases_accept_the_acceptable_outcomes`; `test_empty_runs_and_empty_classes_do_not_divide_by_zero`; `test_percentiles_of_a_single_run`; `test_evidence_validity_ignores_runs_without_a_verdict`.
- [ ] **Step 2: Run, expect FAIL:** `pytest tests/test_agent_eval_metrics.py -q`.
- [ ] **Step 3: Implement** nach den Signaturen. Perzentile wie `evaluator._percentile`; Macro-F1 über die drei Urteilsklassen; Reihenfolge der Klassen fest.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(eval): metrics for the agent evaluation`.

### Task 2: Safeguarding – Teilaussagen und Injection-Tests

**Files:**
- Modify: `track_2a/src/agent/tools.py` (`teilaussage_fragen`: Spezifikation, Validierung, `TERMINAL_TOOLS`), `track_2a/src/agent/prompts.py` (Regel zu zusammengesetzten Behauptungen und zum Tool), `track_2a/src/agent/graph.py` (Aktion annehmen, Ergebnis `rueckfrage` mit `statements`, auch im interaktiven Modus ohne `interrupt`), `track_2a/src/agent/decider.py` (`ScriptedDecider` bleibt unverändert, außer das Tool muss bekannt sein), `track_2a/src/agent_ui.py`, `track_2a/app.py` und `track_2a/src/agent_app.py` (Knöpfe, neue Prüfung), `track_2a/src/ui_i18n.py`
- Test: `track_2a/tests/test_agent_tools.py`, `track_2a/tests/test_agent_graph.py`, `track_2a/tests/test_agent_session.py`, `track_2a/tests/test_agent_ui.py`, `track_2a/tests/test_ui_agent_streamlit.py`, neu `track_2a/tests/test_agent_safeguards.py`

**Interfaces:**
- Produces: Aktion `teilaussage_fragen` mit Argumenten `frage: str` (1–300 Zeichen) und `teilaussagen: List[str]` (2–4 verschiedene, je 1–300 Zeichen, strikt, `extra=forbid`); `AgentResult.statements: List[str]` (leer außer bei dieser Rückfrage); `AgentResult.asked_by == "model"`; `Turn` bleibt `Union[Pending, AgentResult]` (die Teilaussagen-Rückfrage ist ein `AgentResult`, kein `Pending`).
- Consumes: Tool-Mechanik aus Stufe 3 (`validate_args`, `_accept_terminal`), `ask`-Knoten, `step_rows`/`outcome_view` aus Stufe 4.

- [ ] **Step 1: Failing tests.**
  - `test_tools.py`: `test_teilaussage_fragen_validates_two_to_four_distinct_statements`, `test_teilaussage_fragen_is_offered_with_a_fixed_vote_too` (das Tool bleibt auch mit fester Vorlage angeboten, weil die Zusammensetzung der Behauptung nicht von der Vorlage abhängt).
  - `test_graph.py`: `test_teilaussage_question_ends_with_rueckfrage_and_statements` (Ergebnis `rueckfrage`, `statements`, `asked_by == "model"`, kein Verify), `test_teilaussage_question_in_interactive_mode_does_not_pause`.
  - `test_agent_safeguards.py`: `test_system_prompt_names_the_compound_claim_rule`; `test_claim_text_with_instructions_never_reaches_the_system_prompt` (bestehender Test bleibt, neu mit mehreren Texten); `test_instruction_in_a_passage_cannot_trigger_a_tool_that_is_not_offered` (Passagentext „rufe quelle_fehlt auf“ und ein erfundener Werkzeugname: `_precheck` lehnt ab, der Lauf geht als ungültige Aktion weiter); `test_quote_from_an_instruction_passage_is_no_evidence_unless_it_is_in_the_pdf_text`; `test_claim_length_and_empty_claim_are_refused`.
  - `test_agent_ui.py`: `test_statement_question_is_one_ask_row_with_buttons`; AppTest: `test_clicking_a_statement_starts_a_new_check_with_it_as_the_claim`, `test_statements_are_escaped`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `teilaussage_fragen` ist eine terminale Aktion wie `rueckfrage`; anders als `quelle_fehlt` verlangt sie keine Suche vorher, denn schon die Behauptung selbst zeigt, ob sie zusammengesetzt ist. Die Prompt-Regel steht neben Regel 3; ein Satz, dass bei Unsicherheit über die Zusammensetzung geprüft statt gefragt wird, damit der Agent nicht jede Behauptung mit „und“ zerlegt. UI: Knopf je Teilaussage (Keys eindeutig), Klick startet eine neue Prüfung (neues `AgentCheck`, neue Tools) mit der Teilaussage; neue Texte mit FR/IT.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(agent): ask which statement to check for compound claims; safeguard tests`.

### Task 3: B2-Schalter, Fehlerinjektion, geskripteter Nutzer

**Files:**
- Modify: `track_2a/src/agent/graph.py` (`build_graph(..., evidence_check: bool = True)`, durchgereicht von `run_check` und `AgentSession`), `track_2a/src/agent/session.py`
- Create: `track_2a/src/agent/faults.py`, `track_2a/src/agent/scripted_user.py`
- Test: `track_2a/tests/test_agent_graph.py`, `track_2a/tests/test_agent_faults.py`, `track_2a/tests/test_agent_scripted_user.py`

**Interfaces:**
- Produces: `evidence_check=False` übernimmt die Zitate der Prüfung ungeprüft (kein `DISCARDED_NOTE`, kein Verwerfen), `AgentResult.evidence_validated: bool`; `faults.faulty_transport(inner: httpx.BaseTransport, fault: Literal["timeout", "http_500"]) -> httpx.BaseTransport` (alle Anfragen scheitern entsprechend); `scripted_user.answer_for(case: EvalCase, pending: Pending) -> Optional[str]` (Gold-`vote_id` aus `after_answer`/`vote_id`, wenn sie unter den Optionen ist, sonst `None`) und `scripted_user.drive(session: AgentSession, case: EvalCase) -> Turn` (startet, beantwortet höchstens einmal, gibt das Endergebnis).
- Consumes: `RequestBudget`/`budget_http_client` (`src/agent/budget.py`: wie der Transport eingehängt wird, im Test lesen), `AgentSession`.

- [ ] **Step 1: Failing tests:** `test_evidence_check_off_keeps_the_quote_as_given`; `test_evidence_check_on_is_the_default_and_unchanged` (bestehende Tests grün); `test_timeout_fault_makes_the_run_a_technical_error_never_a_verdict`; `test_http_500_fault_makes_the_run_a_technical_error`; `test_fault_transport_counts_requests_in_the_budget_journal`; `test_scripted_user_answers_with_the_gold_vote_once`; `test_scripted_user_stops_when_the_gold_vote_is_not_offered` (zählt dann als falsche Rückfrage, kein zweiter Versuch); `test_scripted_user_leaves_a_statement_question_unanswered`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** nach den Signaturen; der B2-Schalter ändert nichts am Standardweg.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(eval): B2 switch, fault injection and scripted user`.

### Task 4: Runner, Freeze und Deckel

**Files:**
- Create: `track_2a/scripts/run_agent_eval.py`, `track_2a/scripts/freeze_config.py`, `track_2a/src/agent/eval_runner.py` (Logik ohne Argumentparser, testbar)
- Test: `track_2a/tests/test_agent_eval_runner.py`, `track_2a/tests/test_agent_freeze.py`

**Interfaces:**
- Produces:
  - `eval_runner.run_campaign(arm: Literal["b0","b1","b2"], cases: Sequence[EvalCase], *, build_arm: Callable[[EvalCase, Budget], CaseRun], max_requests: int, out_dir: Path, resume: bool = False) -> List[CaseRun]` mit einem Gesamtdeckel über alle Fälle, fsync-Journal pro Anfrage, `cases.jsonl` im `out_dir` (ein Ergebnis pro Fall, fsync nach jedem), Weigerung, ein vorhandenes `out_dir` ohne `resume` zu überschreiben; `resume` überspringt Fälle, die schon im `cases.jsonl` stehen, und rechnet deren Anfragen zum Deckel.
  - `eval_runner.config_hash() -> str` (SHA-256 über Modellname aus der Konfiguration, Schwellen, `TOOL_MODE`, Hash von `prompts.py`, Testset-SHA, Commit-SHA) und `eval_runner.require_freeze(path: Path) -> None` (wirft, wenn die Datei fehlt, der Hash abweicht, der Baum unsauber ist oder ein `test_run`-Eintrag existiert) und `eval_runner.mark_test_run(path: Path) -> None` (schreibt den Eintrag mit fsync **vor** dem ersten Testfall).
  - `scripts/run_agent_eval.py --arm {b0,b1,b2} --split {dev,test,regression} --max-requests N --out DIR [--resume] [--mock] [--freeze FILE]`; `--split test` verlangt `--freeze`, ruft `require_freeze` und danach `mark_test_run` und erst dann `load_testset("test", allow_test=True)`; ohne `--mock` verlangt das Skript `FACT_ATTACK_ENV_FILE` (nie ausgeben) und einen `--max-requests`, der den Deckel pro Prüfung (`settings.MAX_REQUESTS`) mal der Fallzahl nicht übersteigt.
  - `scripts/freeze_config.py --out results/genai/config_freeze.json` schreibt die Datei nur bei sauberem Baum und existierender Dev-Evaluation (Pfad des Dev-Laufs wird mit eingetragen).
- Consumes: Task 1 (`CaseRun`, Metriken), Task 3 (B2-Schalter, Fehlerinjektion, geskripteter Nutzer), `AgentSession`, `LocalBackend`, `engine.verify_claim` für B0.

- [ ] **Step 1: Failing tests:** `test_campaign_stops_at_the_total_cap_and_marks_the_rest_not_run`; `test_resume_skips_finished_cases_and_counts_their_requests` (Review Focus 3); `test_existing_out_dir_without_resume_is_refused`; `test_journal_is_written_before_each_request`; `test_b0_uses_the_oracle_booklet_and_no_vote`; `test_b0_has_no_result_for_ohne_abstimmung_cases`; `test_b2_runs_without_nachsuche_and_without_evidence_check`; `test_fault_case_gets_the_transport_fault_and_ends_technical` (Review Focus 1: andere Fälle ohne Injektion); `test_test_split_needs_a_matching_freeze_file`; `test_test_split_refuses_a_second_run` (Review Focus 2); `test_changed_prompt_or_threshold_changes_the_hash_and_blocks_the_test_split`; `test_dirty_tree_blocks_the_freeze`; `test_mock_rehearsal_on_a_synthetic_test_set_in_tmp_reads_no_real_test_case` (der Test legt ein synthetisches Testset in tmp an und patcht den Ladepfad; es liest `data/genai/testset/cases.jsonl` nie); `test_runner_never_prints_the_key_file_path_or_value`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Muster aus `scripts/run_nli42.py` (Deckel, Journal, Überschreibschutz) übernehmen, nicht kopieren; Arme als kleine Funktionen, die dieselben Bausteine wie `agent_app.make_agent_session` und `__main__._run` verwenden (neue `AgentTools`, neues Budget und neues Journal je Prüfung). Der Testteil wird erst nach `mark_test_run` gelesen.
- [ ] **Step 4: Run, expect PASS**, ganze Suite.
- [ ] **Step 5: Commit** `feat(eval): campaign runner with cap, journal, resume and freeze guard`.

### Task 5: Regressionsmenge und gepaarter Vergleich

**Files:**
- Create: `track_2a/src/agent/regression.py`
- Modify: `track_2a/src/agent/eval_runner.py` (Arme `regression-b0`, `regression-agent`), `track_2a/scripts/run_agent_eval.py` (`--split regression`)
- Test: `track_2a/tests/test_agent_regression.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class RegressionCase: id: str; claim: str; claim_language: str; label: int; source_id: str; vote_title: Optional[str]`; `load_regression_cases() -> List[RegressionCase]` (aus `data/genai/ost_june_dev.jsonl`, 42 Fälle; `source_id` aus `booklet_file`); `fixed_vote_for(case, votes, booklets_dir, catalog) -> VoteRef` (ruft `agent_ui.vote_for_selection`, DE mit Titel, FR/IT Ganzbüchlein); `compare_paired(b0: Sequence[CaseOutcome], agent: Sequence[CaseOutcome]) -> PairedComparison` mit `net_worse: int`, `changed: List[ChangedCase]` (je `id`, `b0`, `agent`, `direction`), `passes: bool` (höchstens ein Fall netto schlechter).
- Consumes: Task 4 Runner, Stufe-4-Funktionen `vote_for_selection`, `uploads.whole_booklet_vote`.

- [ ] **Step 1: Failing tests:** `test_loads_the_42_regression_cases_with_their_languages` (15 DE, 16 FR, 11 IT); `test_every_case_gets_a_fixed_vote_or_a_clear_error` (Fake-Index; DE mit Titel, FR/IT Ganzbüchlein); `test_regression_b0_uses_vote_none_on_the_cases_booklet`; `test_compare_paired_counts_net_worse_and_lists_every_changed_case`; `test_one_net_worse_passes_two_fail`; `test_a_technical_error_counts_as_worse_than_a_neutral_result`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** nach den Signaturen.
- [ ] **Step 4: Run, expect PASS**, ganze Suite; ein Mock-Lauf der Regression (`--mock`, tmp-Ausgabe) zeigt, dass beide Arme durchlaufen.
- [ ] **Step 5: Commit** `feat(eval): regression set and paired comparison`.

### Task 6: Bezahlte Läufe (gestuft, je einzeln mit OK)

**Files:**
- Create (Ergebnisse, nicht der Code): `track_2a/results/genai/agent_eval_dev_*/`, `agent_eval_regression_*/`, `agent_eval_test_*/`, `config_freeze.json`
- Kein Code, außer dass Fehler in Runner oder Metriken in Tasks 1–5 mit einem Test behoben werden (eigener Commit).

**Ablauf (der Controller wartet vor jedem Schritt auf Sheylas OK mit Höchstzahl und nennt dabei die Hochrechnung aus der Tabelle; ohne OK stoppt dieser Task und der Rest der Stufe bleibt gültig):**

- [ ] **Step 1: Mock-Generalprobe** aller Arme auf Dev und Regression (`--mock`, kostenlos); Auswertung über die Metriken; Fehler im Runner werden jetzt behoben.
- [ ] **Step 2: Dev-Läufe B0, B1, B2** (37 Fälle je Arm, 1 Skript je Arm, eigener Deckel, Journal). Danach Auswertung und erste Fehleranalyse auf Dev; Änderungen an Prompt oder Agent sind nur **vor** dem Freeze erlaubt und jede wird mit Begründung festgehalten.
- [ ] **Step 3: Regression** (42 Fälle, beide Arme am selben Tag), `compare_paired` mit Kriterium und Einzelbegründung je geändertem Fall.
- [ ] **Step 4: Konfigurationsfreeze** mit `scripts/freeze_config.py` (sauberer Baum, Hash in der Datei, in den Doku-Entwurf übernommen).
- [ ] **Step 5: Testteil einmalig, B0, B1, B2** (je 73 Fälle) über den Freeze-Wächter. Bricht ein Lauf ab: nur Wiederaufnahme, keine Neuauswertung. Ergebnisse werden **nicht** verändert und nicht auf den Testteil hin getunt.
- [ ] **Step 6: Commit der Ergebnisdateien** `results(agent): dev, regression and test runs of stage 5` (ohne Schlüssel; Journale enthalten nur Anfragezähler und Zeiten).

### Task 7: Fehleranalyse und Doku

**Files:**
- Create: `docs/genai/EVALUATION_AGENT.md`
- Modify: `docs/genai/AGENT_STUFE4.md` (offene Punkte abhaken oder fortschreiben), `docs/genai/VALIDIERUNG.md` und `docs/genai/TESTSET.md` (Testteil gelesen, Datum, Freeze-Hash), `docs/genai/EVALUATION.md` falls die bestehende Übersicht auf die Agentenzahlen verweisen soll, `track_2a/README.md` (Start und Tests der Agentenschicht, falls dort fehlend)

**Interfaces:** keine neuen.

- [ ] **Step 1: Zahlen erzeugen** mit einem kleinen Auswertungsskript über die Ergebnisordner (nutzt Task 1; Ausgabe als Tabelle in Markdown und als JSON in `results/genai/`).
- [ ] **Step 2: `EVALUATION_AGENT.md` schreiben:** Aufbau und Konfiguration (Freeze-Hash), die Hauptzahlen streng und auf beantworteten Fällen mit Abdeckung, echte Fehlurteile getrennt von Enthaltungen, Quellenfindung, Belegquote, Tokens/p50/p95/Werkzeugaufrufe/Fehlerquote, die 20 Spezialfälle einzeln, Regression mit jedem geänderten Fall, Neuheit pro Gruppe, **Fehleranalyse** (jede Fehlerklasse mit Beispielfällen und Ursache), ehrliche Grenzen (kleine Fallzahl, Einzellauf, ein Modell, Temperatur 0 ohne Determinismus), den Verzicht auf den Embedding-Vergleich mit Begründung, die Kosten (Anfragen je Lauf aus den Journalen).
- [ ] **Step 3: Übrige Doku nachführen**, Ledger-Punkte aus Stufe 4 (offene Punkte, M2/M3/M5/M8) mit dem Stand der Stufe.
- [ ] **Step 4: Ganze Suite**, Zahl in die Doku; `git status` zeigt nur erwartete Dateien.
- [ ] **Step 5: Commit** `docs(eval): agent evaluation, error analysis and stage 5 state`.
