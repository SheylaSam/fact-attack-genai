# Agentenschicht Stufe 3: Rückfrage, Nachsuche, Budget, MCP – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Agent rät keine Vorlage mehr, sondern fragt deterministisch zurück. `quelle_fehlt` gibt es nur nach einer Suche. Nach einem „nicht belegt“ sucht er genau einmal nach und prüft ein zweites und letztes Mal. Seine drei Suchwerkzeuge laufen wahlweise über einen eigenen MCP-Server.

**Architecture:** Ein `SearchBackend` trennt die drei Suchwerkzeuge vom Transport. `LocalBackend` arbeitet mit Index und Engine im selben Prozess, `McpBackend` mit einem eigenen FastMCP-Server über stdio, angebunden mit `langchain-mcp-adapters`. Der Graph bekommt drei Neuerungen:
- einen Wächter (`guard.py`), der die Vorlagen-Rangliste der Behauptung mit festen Schwellen einschätzt;
- einen Knoten `ask`, durch den jede Rückfrage läuft (Stufe 4 setzt dort `interrupt` ein);
- einen Knoten `nachsuche` mit genau einer Rückkante zur Suche.

`src/inference.py` bleibt unverändert.

**Tech Stack:** Python 3.12.13, LangGraph 1.2.11, langchain-openai 1.6.2, `mcp` 1.30.0 (FastMCP, stdio), `langchain-mcp-adapters` 0.3.2, Qdrant + `intfloat/multilingual-e5-small` wie in Stufe 1, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-agentenschicht-design.md`, Abschnitte „Ablauf und Werkzeuge“, „Budget“, „MCP, Memory, Safeguarding, Protokoll“, „Evaluation“ und „Stufen“ (Stufe 3, Ziel So 08.11.2026, SW6/SW7). Grundlage sind außerdem `docs/genai/AGENT_STUFE1.md` (Befunde der echten Läufe) und der Plan von Stufe 2 (`2026-11-01-agentenschicht-stufe2-testset.md`).

**Branch:** `genai/agent-stufe3`. Er wird von `genai/agent-testset` abgezweigt, sobald dort Stufe-2-Task 4 committet ist; dann existieren `graph.page_texts` und `testset.load_testset`. Vor Task 8 wird `genai/agent-testset` mit dem gesperrten Testset hineingemergt.

**Nicht in Stufe 3:**
- Oberfläche, Checkpointer, `interrupt` und Sitzungs-Memory (Stufe 4)
- Safeguarding inklusive zusammengesetzter Behauptungen (Stufe 5)
- Evaluation B0/B1/B2 und Embedding-Vergleich (Stufe 5)
- automatische Quellenfindung auf Französisch und Italienisch

**Reihenfolge:** Tasks 1–7 brauchen das gesperrte Testset nicht und starten sofort. Task 8 läuft erst nach dem Sperren (geplant So 08.11.). Verschiebt sich das Sperren, läuft Task 9 mit den vorläufigen Schwellen und hält das fest. Kürzbar ist in dieser Stufe nur Task 4 (Nachsuche, Punkt 4 der Kürzungsreihenfolge, also erst nach den Punkten 1–3).

## Entscheidungen dieses Plans (bitte prüfen)

| Frage | Entscheidung | Grund |
|---|---|---|
| Bewährte Suche: Behauptung oder Anfrage des Agenten? | **Behauptung.** `suche_passagen` nimmt nur noch `vorlage_id`; die eigene Formulierung des Agenten geht nur an `suche_passagen_vektor`. | Die Abschnittslogik wertet die Signalwörter der Behauptung aus. Mit vorgegebener Vorlage findet die Suche damit genau die Stellen von B0, eingeschränkt auf die Seiten der Vorlage. Das ist die Voraussetzung für das Regressionskriterium in Stufe 5. |
| Woran entscheidet der Wächter? | An der Rangliste von `suche_vorlagen` **mit dem Wortlaut der Behauptung**, nicht mit der Anfrage des Modells. Er greift bei der ersten vorlagengebundenen Aktion. Ist die Lage mehrdeutig, endet der Lauf mit `rueckfrage` und den nahen Vorlagen als Optionen (2–4), egal welche Vorlage das Modell gewählt hat. | Lauf 1 von Stufe 1 zeigt: Die Rangfolge hängt vom Suchtext ab, den das Modell formuliert. Greift der Wächter erst bei der ersten Festlegung, kann das Modell vorher noch selbst `quelle_fehlt` wählen; Themen ohne Vorlage enden so nicht in einer falschen Rückfrage. |
| Vorläufige Schwellen | Nah ist, wer weniger als 0,02 Score hinter Rang 1 liegt **und** mindestens halb so viele Treffer hat. „keine“ heißt: kein Kandidat. | Gemessen an Stufe 1: Mietrecht Δ 0,007 (mehrdeutig), Zivildienst Δ 0,037, Untermiete Δ 0,047 (eindeutig). Kalibriert wird nur in Task 8 und nur auf Dev. |
| Schwelle für „keine“ | Wird nicht kalibriert und bleibt `None`. | Dev hat genau einen Fall `ohne_abstimmung`. |
| Antwort auf eine Rückfrage (CLI, Evaluation) | Eine **neue Prüfung mit `--quelle <gewählte ID>`**, mit eigenem Deckel. Der geskriptete Nutzer der Evaluation macht dasselbe mit der Gold-Vorlage. | Ohne Checkpointer gibt es kein Fortsetzen. Jede Rückfrage läuft durch den Knoten `ask`. Stufe 4 ersetzt dort das Beenden durch `interrupt`, danach teilen sich beide Teile einen Deckel. |
| Nachsuche | Wird ausgelöst von „nicht belegt“ nach der ersten Prüfung, auch durch ein verworfenes Urteil. Angeboten wird nur `suche_passagen_vektor` in der geprüften Vorlage, für genau eine Entscheidung. Ein ungültiger Schritt ist `technischer_fehler`. Gibt es keine neuen Passagen, entfällt die zweite Prüfung und das erste „nicht belegt“ bleibt. | `suche_passagen` liefert mit der Behauptung immer dieselben Stellen. Das Design verlangt: Grenze erreicht → technischer Fehler. Eine zweite Prüfung ohne neue Stellen wäre eine verschwendete Anfrage. |
| MCP-Transport | Wird per `--werkzeuge lokal\|mcp` gewählt, **Standard `lokal`**. Der Server hält Index und Engine mit einem Mock-Client und sendet nie eine Apertus-Anfrage. Eine sprachübergreifende bewährte Suche (Übersetzung nötig) lehnt er ab. | Der Deckel zählt nur Anfragen im Agentenprozess; eine Übersetzung im Server wäre ungezählt. FR/IT mit vorgegebener Quelle bleibt deshalb vorerst nur lokal möglich. |
| Natives Tool-Calling | `tool_choice="required"`. `native` wird nur Standard, wenn 3 von 3 Anfragen angenommen werden, gültig sind und das erwartete Werkzeug wählen. Sonst bleibt `json`. | Ein Wechsel weg von einem funktionierenden Standard braucht mehr als Gleichstand. |

## Global Constraints

- Arbeitsverzeichnis ist der Repo-Root. Tests laufen mit `cd track_2a && ../.venv312/bin/python -m pytest -q`. Mit echtem E5: `HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1`.
- B0 bleibt byte-identisch:
  - keine Änderung an `src/inference.py`, `src/apertus_client.py`, `src/http_chat.py` und der Wettbewerbs-CLI;
  - `tests/test_apertus_client.py` und alle bestehenden Tests bleiben grün.
- `scripts/agent_tool_smoke.py` und seine Ergebnisdateien bleiben unverändert; sie sind der Stand von Stufe 1.
- `track_2a/requirements.txt` bleibt unverändert. Neue Pakete kommen nach `track_2a/requirements-agent.txt` und ins Hash-Lock `track_2a/requirements-genai312-macos-arm64.lock.txt`. Gepinnt wird, was getestet ist; Kursversionen sind `mcp` 1.30.0 und `langchain-mcp-adapters` 0.3.2.
- Für jeden kostenpflichtigen Apertus-Lauf gilt:
  - harter Anfrage-Deckel und Journal;
  - vorher ausdrückliches OK von Sheyla (Task 7: höchstens 4 Anfragen, Task 9: höchstens 60);
  - die Schlüsseldatei aus `FACT_ATTACK_ENV_FILE` wird nie ausgegeben, geloggt, committet oder an den MCP-Server weitergegeben.
- Es gibt genau sechs Ausgänge wie `graph.Outcome`: `gestuetzt`, `widersprochen`, `nicht_belegt`, `rueckfrage`, `quelle_fehlt`, `technischer_fehler`. Ein technisches Problem wird nie `nicht_belegt`. Das gilt auch für MCP-Start, MCP-Absturz, einen Fehler in der Nachsuche und ein erschöpftes Budget.
- Grenzen pro Prüfung:

  | Grenze | Wert |
  |---|---|
  | Suchschritte vor der ersten Prüfung (`MAX_STEPS`) | 5 |
  | Suchschritt in der Nachsuche (`MAX_RESEARCH_STEPS`) | 1 |
  | Prüfungen (`MAX_CHECKS`) | 2 |
  | tatsächliche HTTP-Anfragen (`MAX_REQUESTS`) | 12 |

  Wird eine Grenze erreicht, ohne dass die Suche ordentlich endet, ist das Ergebnis `technischer_fehler`.
- Schwellen des Wächters werden nur aus `load_testset("dev")` kalibriert, nie aus dem Testteil. Bis dahin gelten die vorläufigen Werte (0,02 / 0,5 / `None`).
- Kein Fall aus `track_2a/data/genai/testset/cases.jsonl` geht vor dem Sperren durch Agent, Index oder Modell. Tests und Probeläufe verwenden erfundene Behauptungen oder die drei Behauptungen aus Stufe 1.
- MCP: eigener FastMCP-Server über stdio mit genau `suche_vorlagen`, `suche_passagen` und `suche_passagen_vektor`. `rueckfrage`, `quelle_fehlt` und `pruefen` bleiben im Graphen. Startet der Server nicht, ist das ein sichtbarer `technischer_fehler`; es gibt keinen stillen Wechsel auf lokal.
- Der Graph bleibt synchron (`invoke`); nur so zählt das Budget jede Anfrage.
- Doku und Meldungen an Menschen und an das Modell sind deutsch, Code-Kommentare englisch. Werkzeug- und Knotennamen sind wie oben.
- Neue Tests folgen dem Stil von `tests/test_agent_graph.py`: `unittest`, `sys.path.insert(0, parents[1])`, `skipUnless` für optionale Pakete (`langgraph`, `mcp`, `langchain_mcp_adapters`).

## Review Focus

1. **Die Anfrage des Modells verdeckt die Mehrdeutigkeit.** Das Modell sucht mit „Untermiete schriftliche Zustimmung“, obwohl die Behauptung nur „die Mietrechtsvorlage“ nennt. Der Wächter muss trotzdem nachfragen. Test in Task 3.
2. **Der Pool ist vor der Nachsuche schon voll** (10 geprüfte Passagen). Die neuen Stellen der Nachsuche müssen trotzdem in die zweite Prüfung kommen, und zwar zuerst. Test in Task 4.
3. **Der MCP-Server stirbt oder hängt mitten in der Prüfung.** Ergebnis ist `technischer_fehler` innerhalb des Aufruf-Timeouts, kein Hängen und kein „nicht belegt“. Test in Task 6.
4. **Eine Bibliothek schreibt beim Start des Servers auf stdout.** Das darf das stdio-Protokoll nicht zerstören. Test in Task 5.
5. **Französische Behauptung mit vorgegebener deutscher Vorlage über MCP.** Ergebnis ist ein sichtbarer Fehler und keine stille Suche ohne Übersetzung. Test in Task 5.

---

### Task 1: Such-Backend, bewährte Suche mit der Behauptung, Pool nur aus den Seiten der Vorlage

**Files:**
- Create: `track_2a/src/agent/search_backend.py`
- Modify: `track_2a/src/agent/tools.py`
  - `AgentTools`-Konstruktor
  - `_SearchPassagesArgs` aufteilen in `_ProvenSearchArgs(vorlage_id)` und `_VectorSearchArgs(vorlage_id, anfrage)`
  - `run(..., claim=...)`
  - Seitenfilter in `_pooled`
  - Beschreibung von `suche_passagen`
- Modify: `track_2a/src/agent/graph.py` (`act` übergibt `claim`; `_valid_evidence` verwirft Zitate von Seiten außerhalb von `vote.pages`)
- Modify: `track_2a/src/agent/decider.py` (`ScriptedDecider`: `suche_passagen` nur mit `vorlage_id`)
- Modify: `track_2a/src/agent/__main__.py` (`LocalBackend` verdrahten)
- Test: `track_2a/tests/test_agent_tools.py`, `test_agent_graph.py`, `test_agent_decider.py`, `test_agent_cli.py`

**Interfaces:**
- Produces in `src/agent/search_backend.py`:

```python
class SearchBackend(Protocol):
    votes: Dict[str, VoteRef]
    def tool_specs(self) -> List[dict]: ...            # model-facing specs of the three search tools, TOOL_SPECS order
    def search_votes(self, query: str, k: int = 5) -> List[VoteCandidate]: ...
    def proven_passages(self, vote_id: str, claim: str) -> List[Dict[str, Any]]: ...  # unfiltered, engine order
    def vector_passages(self, vote_id: str, query: str, k: int = 8) -> List[Dict[str, Any]]: ...
    def close(self) -> None: ...

class LocalBackend:  # index and engine in this process; close() is a no-op (the CLI owns both)
    def __init__(self, index: SourceIndex, engine, booklets_dir: Path): ...
```

  `proven_passages` ruft `engine.retrieve_passages(claim=claim, booklet_pdf=booklets_dir / f"{vote.source_id}.pdf", vote=engine_vote(vote), booklet_language=vote.language)` auf.
- Produces in `tools.py`:
  - `AgentTools(backend: SearchBackend, booklets_dir: Path, *, fixed_vote_id: Optional[str] = None)`
  - Property `votes` (aus dem Backend)
  - `run(self, tool: str, args: dict, *, claim: Optional[str] = None) -> Observation`
  - Werkzeugargumente: `suche_passagen(vorlage_id)`, `suche_passagen_vektor(vorlage_id, anfrage)` (unverändert). `available_specs()` setzt die Suchwerkzeuge aus `backend.tool_specs()` und die Endwerkzeuge aus `TOOL_SPECS` zusammen, in der Reihenfolge von `TOOL_SPECS`.
  - Neue Beschreibung von `suche_passagen`: „Sucht mit dem Wortlaut der Behauptung die Textstellen der gewählten Vorlage (bewährte Suche mit Stichwörtern und Abschnittslogik, wie im bewährten Prüfpfad). In den Passagenpool kommen nur Stellen von den Seiten dieser Vorlage. Für dieselbe Vorlage liefert sie immer dieselben Stellen; für eine andere Formulierung suche_passagen_vektor verwenden.“
  - `_pooled`: Eine Passage, deren Seite nicht in `vote.pages` liegt, bekommt den Status `"andere Vorlage, nicht aufgenommen"` und kommt nicht in den Pool. Die Daten erhalten `outside: int`, der Kopf nennt die Zahl.
  - `suche_passagen` ohne `claim` ergibt `ERROR_TOOL_FAILURE` („Behauptung fehlt“). Der Graph übergibt sie immer.
- Consumes: `engine_vote`, `VoteRef`, `VoteCandidate`, `TOOL_SPECS`

- [ ] **Step 1: Failing tests schreiben.** Die Testhilfen bauen künftig `AgentTools(LocalBackend(index, engine, BOOKLETS), BOOKLETS)`, auch `RealEngineTests.run_real`. Bestehende Tests werden so angepasst:
  - Wer `suche_passagen` mit `anfrage` aufruft, bekommt die neue Signatur; Aufrufe von `retrieve_passages` werden dann auf die Behauptung geprüft.
  - Der Seitenfilter gilt für jede Suche, die in den Pool schreibt. Bestehende Tests, deren Fake-Passagen außerhalb der Seiten ihrer Vorlage liegen, bekommen eine Fake-Vorlage mit passenden Seiten.
  - Keine andere Zusicherung wird abgeschwächt.

```python
# tests/test_agent_tools.py
def test_suche_passagen_runs_the_proven_search_with_the_claim(self):
    tools.run('suche_passagen', {'vorlage_id': V1.vote_id}, claim=CLAIM)
    call = engine.calls[-1]
    assert call['claim'] == CLAIM and call['vote'] == V1.title and call['booklet_language'] == 'de'
    assert call['booklet_pdf'] == BOOKLETS / '2024-11-24_de.pdf'
def test_suche_passagen_takes_no_model_query(self): ...       # validate_args(..., {'vorlage_id':…, 'anfrage':'x'}) -> ValueError "'anfrage' ist kein Argument"
def test_suche_passagen_without_claim_is_a_tool_failure(self): ...  # obs.data['error_kind'] == ERROR_TOOL_FAILURE
def test_passages_outside_the_vote_pages_are_not_pooled(self):     # V1.pages == (2, 3); die Engine liefert S. 2 und S. 4
    obs = tools.run('suche_passagen', {'vorlage_id': V1.vote_id}, claim=CLAIM)
    assert [p['page_number'] for p in tools.passages_for(V1.vote_id)] == [2]
    assert obs.data['outside'] == 1 and 'andere Vorlage, nicht aufgenommen' in obs.text
def test_whole_booklet_vote_pools_every_page(self): ...         # '#0' mit allen Seiten: nichts wird gefiltert
def test_local_backend_specs_are_the_three_search_tools(self):
    assert [s['name'] for s in backend.tool_specs()] == ['suche_vorlagen', 'suche_passagen', 'suche_passagen_vektor']
# tests/test_agent_graph.py
def test_act_passes_the_claim_to_the_proven_search(self): ...    # FakeEngine-Aufruf hat claim == CLAIM
def test_quote_on_a_page_of_another_vote_is_no_evidence(self): ...  # Zitat steht wörtlich auf S. 4 (V2), geprüft wird V1 -> nicht_belegt, note == NOTE
def test_proven_search_of_a_fixed_vote_is_b0_retrieval_on_the_vote_pages(self):  # RealEngineTests, 2026-06-14_de
    expected = [p for p in engine.retrieve_passages(CLAIM_ZD, BOOKLET, vote=vote.title, booklet_language='de')
                if p['page_number'] in vote.pages][:settings.MAX_PASSAGES]
    tools.run('suche_passagen', {'vorlage_id': vote.vote_id}, claim=CLAIM_ZD)
    assert tools.passages_for(vote.vote_id) == expected
# tests/test_agent_decider.py
def test_scripted_searches_passages_with_the_vote_only(self): ...  # action.args == {'vorlage_id': '2024-11-24_de#1'}
```

  `CLAIM_ZD = "Wer in den Zivildienst wechselt, muss künftig mindestens 150 Diensttage leisten."` (Lauf 2 aus Stufe 1). Die `VoteRef` für `2026-06-14_de#2` entsteht aus `engine.booklet_layout(BOOKLET)` und `source_index._booklet_votes` mit dem Katalogeintrag.
- [ ] **Step 2:** `cd track_2a && ../.venv312/bin/python -m pytest tests/test_agent_tools.py tests/test_agent_graph.py tests/test_agent_decider.py -q` → FAIL (`LocalBackend` fehlt, `claim` unbekannt).
- [ ] **Step 3: Implementieren.** Das Modul `search_backend.py` erhält die Signaturen oben. `AgentTools` bekommt keinen Index und keine Engine mehr direkt. `act` ruft `self.tools.run(tool, action.args, claim=state["claim"])` auf. Das Zitat einer Seite außerhalb von `vote.pages` zählt in `_valid_evidence` wie ein nicht gefundenes Zitat. `__main__.py` baut `LocalBackend(index, engine, config.BOOKLETS_DIR)`.
- [ ] **Step 4:** Die drei Dateien → PASS; danach die ganze Suite grün.
- [ ] **Step 5: Commit** `feat(agent): run the proven passage search with the claim and pool only pages of the chosen vote`

---

### Task 2: Wächter – Einschätzung der Vorlagenkandidaten

**Files:**
- Create: `track_2a/src/agent/guard.py`
- Modify: `track_2a/src/agent/settings.py`
- Modify: `track_2a/src/agent/tools.py` (`SCORE_HINT` entfernen, Einschätzung in die Beobachtung von `suche_vorlagen`, `claim_assessment`)
- Test: `track_2a/tests/test_agent_guard.py`, `test_agent_tools.py`, `test_agent_settings.py`

**Interfaces:**
- Produces in `settings.py`, kommentiert als „vorläufig bis zur Kalibrierung auf Dev (Task 8 von Stufe 3)“:
  - `GUARD_SCORE_MARGIN: float = 0.02`
  - `GUARD_MIN_HITS_RATIO: float = 0.5`
  - `GUARD_NONE_BELOW: Optional[float] = None`
- Produces in `guard.py`:

```python
Kind = Literal["eindeutig", "mehrdeutig", "keine"]

@dataclass(frozen=True)
class Thresholds:
    score_margin: float
    min_hits_ratio: float
    none_below: Optional[float]

def current_thresholds() -> Thresholds  # from settings

@dataclass(frozen=True)
class Assessment:
    kind: Kind
    top: Optional[str]                   # vote id of rank 1
    close: Tuple[str, ...]               # rank order, top included; >= 2 exactly when kind == "mehrdeutig"
    candidates: Tuple[VoteCandidate, ...]
    def to_data(self) -> dict            # {"kind", "top", "close", "thresholds": {...}}, JSON-safe

def assess(candidates: Sequence[VoteCandidate], thresholds: Optional[Thresholds] = None) -> Assessment
def question(options: Sequence[VoteRef]) -> str   # German question naming titles (cut at 90 chars) and dates
def hint(assessment: Assessment) -> str           # the German line for the suche_vorlagen observation
```

- Regel von `assess`:

```text
keine Kandidaten                                      -> keine
none_below gesetzt und top.score < none_below         -> keine
close = Kandidaten unter den ersten 4 (OPTION_RANGE[1]) mit
        top.score - c.score < score_margin  (strikt)  und  c.hits >= min_hits_ratio * top.hits
len(close) >= 2 -> mehrdeutig, sonst eindeutig
```

- `question`:
  - bei 2 Optionen: „Welche Vorlage ist gemeint: «A» (TT.MM.JJJJ) oder «B» (TT.MM.JJJJ)?“
  - bei 3–4 Optionen: Komma-Liste mit „oder“ vor der letzten Option
- `hint`:
  - eindeutig: „Einschätzung: {top} passt klar am besten.“
  - mehrdeutig: „Einschätzung: mehrdeutig, {ids} liegen gleichauf. Lässt die Behauptung nicht erkennen, welche gemeint ist, beende mit rueckfrage und diesen Optionen.“
  - keine: „Einschätzung: keine passende Vorlage. Beende mit quelle_fehlt.“
- Produces in `tools.py`:
  - `AgentTools.claim_assessment(self, claim: str) -> Assessment`. Sie ruft `backend.search_votes(claim[:MAX_QUERY_CHARS].strip(), k=VOTE_CANDIDATES)` einmal pro Instanz auf (Cache) und reicht Ausnahmen weiter.
  - Die Beobachtung von `suche_vorlagen` endet mit `hint(...)` und hat `data["assessment"]`.
  - `SCORE_HINT` gibt es nicht mehr (Entscheidung R15 aus Stufe 1).

- [ ] **Step 1: Failing tests schreiben.** Die Zahlen stammen aus Stufe 1 (Smoke-Situation b und Lauf 2).

```python
RENT = [cand('2024-11-24_de#3', .867, 15), cand('2020-02-09_de#1', .860, 13), cand('2024-11-24_de#2', .860, 14),
        cand('2021-11-28_de#3', .848, 1), cand('2022-05-15_de#3', .845, 3)]
def test_close_rent_votes_are_ambiguous():
    a = assess(RENT, Thresholds(0.02, 0.5, None))
    assert a.kind == 'mehrdeutig' and a.close == ('2024-11-24_de#3', '2020-02-09_de#1', '2024-11-24_de#2')
def test_clear_lead_is_unambiguous():   # Zivildienst .892/23 vor .855/5 -> eindeutig, close == (top,)
def test_margin_is_strict():            # Thresholds(0.25, 0, None), Scores 0.75 und 0.5 -> eindeutig
def test_few_hits_are_not_close():      # top 15 Treffer, Ratio 0.5: 7 Treffer nicht nah, 8 Treffer nah
def test_close_set_is_capped_at_four(): # 6 gleichauf -> len(close) == 4, Rangfolge
def test_no_candidates_is_keine():
def test_weak_top_is_keine_with_none_below():  # Thresholds(0.02, 0.5, 0.9), top .85 -> keine
def test_question_names_titles_and_dates():    # 3 Optionen -> "«A» (24.11.2024), «B» (09.02.2020) oder «C» (24.11.2024)?"
def test_guard_defaults():                     # settings: 0.02, 0.5, None
# tests/test_agent_tools.py
def test_suche_vorlagen_states_the_assessment_instead_of_the_score_hint(self):
    assert 'mehrdeutig' in obs.text and 'eng beieinander' not in obs.text
    assert obs.data['assessment']['kind'] == 'mehrdeutig' and not hasattr(tools_module, 'SCORE_HINT')
def test_claim_assessment_searches_with_the_claim_once(self):  # zwei Aufrufe -> index.vote_calls == [(CLAIM, 5)]
```

- [ ] **Step 2:** `../.venv312/bin/python -m pytest tests/test_agent_guard.py tests/test_agent_tools.py tests/test_agent_settings.py -q` → FAIL.
- [ ] **Step 3: Implementieren** wie in den Interfaces.
- [ ] **Step 4:** Dateien → PASS, ganze Suite grün.
- [ ] **Step 5: Commit** `feat(agent): assess vote candidates with fixed thresholds instead of a score hint`

---

### Task 3: Rückfrage durch den Wächter, `quelle_fehlt` erst nach der Suche, Antwort per `--quelle`

**Files:**
- Modify: `track_2a/src/agent/graph.py` (Knoten `ask`, Wächter in `act`, Regel für `quelle_fehlt`, `AgentResult.asked_by`)
- Modify: `track_2a/src/agent/prompts.py` (Regel 4)
- Modify: `track_2a/src/agent/cli_format.py` (`NODE_LABELS["ask"] = "Rückfrage"`, `answer_commands`, `format_result`)
- Modify: `track_2a/src/agent/__main__.py` (übergibt Behauptung und Laufoptionen an `format_result`)
- Test: `track_2a/tests/test_agent_graph.py`, `test_agent_cli.py`

**Interfaces:**
- Consumes: `AgentTools.claim_assessment`, `guard.question`, `Assessment.to_data` (Task 2)
- Produces:
  - `AgentResult.asked_by: Optional[Literal["guard", "model"]] = None`
  - Topologie `act → ask → finish`. `ask` ist der einzige Knoten, der `outcome="rueckfrage"` setzt. Er emittiert ein Ereignis `node="ask"`, `tool="rueckfrage"`, `args={"optionen": [...]}` und `data` (Einschätzung bzw. Optionen). Docstring: „Stufe 4 ersetzt das Beenden hier durch `interrupt` und setzt die Prüfung mit der gewählten Vorlage fort.“
  - **Wächter.** Ohne feste Vorlage prüft der Graph vor der ersten vorlagengebundenen Aktion einmal `tools.claim_assessment(claim)`. Vorlagengebunden sind `suche_passagen`, `suche_passagen_vektor` und `pruefen`, jeweils mit gültigen Argumenten und bekannter ID.
    - `mehrdeutig`: Die Aktion wird nicht ausgeführt. Die Route geht zu `ask` mit den Optionen `close`, der Frage `guard.question(...)` und `asked_by="guard"`.
    - Sonst setzt der Graph `guard_done` und führt die Aktion aus. Das `act`-Ereignis trägt dann `data["guard"] = assessment.to_data()`.
    - Wirft `claim_assessment` eine Ausnahme, endet der Lauf als `technischer_fehler` mit der Meldung „Wächter: Vorlagensuche zur Behauptung fehlgeschlagen: …“.
  - Eine Rückfrage des Modells geht ebenfalls über `ask`, mit `asked_by="model"`.
  - **`quelle_fehlt`** wird nur angenommen, wenn eine Suche vorausging:
    - ohne feste Vorlage: mindestens ein `suche_vorlagen` ohne Fehler, sonst Ablehnung „quelle_fehlt erst nach einer Suche: Rufe zuerst suche_vorlagen auf.“;
    - mit fester Vorlage: mindestens eine Passagensuche, sonst „… Suche zuerst mit suche_passagen oder suche_passagen_vektor.“
    - Eine Ablehnung ist eine Beobachtung und zählt als Suchschritt.
  - Neue State-Felder: `vote_searches: int`, `passage_searches: int`, `guard_done: bool`, `asked_by: str`.
  - Neue Regel 4 im Systemprompt: „Passt keine Vorlage, beende mit quelle_fehlt; das geht erst, nachdem suche_vorlagen gelaufen ist.“
  - `ScriptedDecider` fragt nicht mehr selbst nach; `DEFAULT_MARGIN` entfällt. Er nimmt wie das Modell in Lauf 1 Rang 1, und der Wächter fängt das ab. Damit gibt es eine einzige Quelle für Mehrdeutigkeit, und der Mock-Lauf zeigt den Wächter. `test_scripted_asks_when_top_two_close` und `test_scripted_margin_is_a_strict_lower_bound` werden durch `test_scripted_takes_rank_one_and_leaves_the_question_to_the_guard` ersetzt.
  - `answer_commands(claim: str, options: Sequence[VoteRef], extra_args: Sequence[str] = ()) -> List[str]`. Jede Zeile lautet `python -m src.agent <shlex.quote(claim)> --quelle <id>` plus `extra_args`.
  - `format_result(result, *, claim: Optional[str] = None, answer_args: Sequence[str] = ())` zeigt bei `rueckfrage` unter den Optionen „Antworten mit einer neuen Prüfung:“ und die Befehle. Die CLI übergibt `--mock` bzw. `--werkzeuge …`, wenn gesetzt.

- [ ] **Step 1: Failing tests schreiben.** Die Fakes in `test_agent_graph.py`:
  - `FakeIndex` bekommt `candidates_by_query` (Anfrage → Kandidaten); `vote_calls` zeichnet wie bisher die Anfragen auf.
  - `FakeEngine` zeichnet `retrieve_calls` auf.
  - `CLOSE` ist V1/V2 gleichauf, `CLEAR` hat V1 klar vorn.
  - Bestehende Tests, die `vote_calls` zählen, berücksichtigen die zusätzliche Suche mit der Behauptung.

```python
def test_guard_asks_when_the_claim_fits_several_votes(self):  # PresetDecider: suche_vorlagen, suche_passagen(V1)
    assert result.outcome == 'rueckfrage' and result.asked_by == 'guard'
    assert [o.vote_id for o in result.options] == [V1.vote_id, V2.vote_id]
    assert engine.retrieve_calls == [] and engine.verify_calls == []
def test_guard_uses_the_claim_not_the_model_query(self):      # Review Focus 1
    index = FakeIndex(candidates_by_query={CLAIM: CLOSE, 'Untermiete schriftliche Zustimmung': CLEAR})
    # Modell sucht mit der klaren Anfrage, dann suche_passagen(V1) -> rueckfrage, asked_by 'guard', CLAIM in index.vote_calls
def test_guard_lets_a_clear_claim_through(self): ...          # CLEAR -> gestuetzt; claim_assessment genau einmal
def test_guard_is_skipped_with_a_fixed_vote(self): ...        # feste Vorlage -> CLAIM nicht in vote_calls
def test_model_rueckfrage_goes_through_ask(self): ...         # asked_by == 'model', 'ask' in {e.node for e in events}
def test_quelle_fehlt_before_any_search_is_rejected(self):    # Euro-Lauf aus Stufe 1
    # PresetDecider: quelle_fehlt, suche_vorlagen, quelle_fehlt -> outcome quelle_fehlt, steps == 3,
    # decider.calls[1]['messages'][-1]['content'] enthält 'zuerst suche_vorlagen'
def test_quelle_fehlt_with_fixed_vote_needs_a_passage_search(self): ...
def test_guard_failure_is_technical_error(self): ...          # Index wirft bei CLAIM -> technischer_fehler, 'Wächter' in error
def test_answer_by_fixed_vote_continues_to_a_verdict(self):
    # nach einer Wächter-Rückfrage: run_check mit AgentTools(..., fixed_vote_id=options[1].vote_id) und
    # ScriptedDecider(fixed_vote_id=...) -> gestuetzt, verify_calls[0]['vote'] == V2.title
# tests/test_agent_cli.py
def test_format_steps_marks_the_question_step(self): ...      # Zeile 'Rückfrage' mit '?'
def test_answer_commands_quote_the_claim(self):
    lines = answer_commands('Er sagt "nein" & geht', [V2], ['--mock'])
    assert lines == [f"python -m src.agent {shlex.quote('Er sagt \"nein\" & geht')} --quelle {V2.vote_id} --mock"]
def test_result_json_has_asked_by(self): ...
```

  Bestehende Tests zur Rückfrage (`test_rueckfrage_returns_question_and_options`, `test_main_mock_asks_back_without_network`) werden angepasst: Die Rückfrage hat jetzt ein eigenes `ask`-Ereignis.
- [ ] **Step 2:** `../.venv312/bin/python -m pytest tests/test_agent_graph.py tests/test_agent_cli.py -q` → FAIL.
- [ ] **Step 3: Implementieren.** `_accept_terminal` bekommt den State, damit es die Regel für `quelle_fehlt` prüfen kann. Die Wächterprüfung sitzt in `act` vor `_accept_terminal` bzw. vor `tools.run`. `build_graph` registriert `ask`.
- [ ] **Step 4:** Dateien → PASS, ganze Suite grün.
- [ ] **Step 5: Mock-Lauf** (ohne Netz):

```bash
cd track_2a && HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python -m src.agent "Die Mietrechtsvorlage stärkt die Vermieter." --mock
```

  Expected:
  - Der Skriptentscheider wählt nach `suche_vorlagen` Rang 1, der Wächter fängt ab, und die Schrittliste endet mit `? Rückfrage`.
  - Die Optionen sind die nahen Vorlagen der Behauptung, darunter beide Mietrecht-Vorlagen vom 24.11.2024, und unter jeder Option steht ein Befehl mit `--quelle … --mock`.
  - Weichen die Optionen ab (etwa die Wohnungsinitiative von 2020 als weitere Option), wird das im Report vermerkt und nicht kaschiert.
- [ ] **Step 6: Commit** `feat(agent): ask back deterministically when the claim fits several votes and require a search before quelle_fehlt`

---

### Task 4: Nachsuche nach „nicht belegt“ und Prüfbudget

**Files:**
- Modify: `track_2a/src/agent/settings.py` (`MAX_CHECKS = 2`, `MAX_RESEARCH_STEPS = 1`)
- Modify: `track_2a/src/agent/tools.py` (`POOL_LIMIT`, `recheck_passages`, Pooltext)
- Modify: `track_2a/src/agent/graph.py` (Knoten `nachsuche`, Phase, Zähler `checks`, Rekursionsgrenze, `nachsuche`-Schalter)
- Modify: `track_2a/src/agent/cli_format.py` (`NODE_LABELS["nachsuche"] = "Nachsuche"`)
- Modify: `track_2a/src/agent/decider.py` (`ScriptedDecider` in der Nachsuche)
- Test: `track_2a/tests/test_agent_tools.py`, `test_agent_graph.py`, `test_agent_decider.py`, `test_agent_settings.py`, `test_agent_cli.py`

**Interfaces:**
- Produces in `tools.py`:
  - `POOL_LIMIT = 2 * settings.MAX_PASSAGES`: So viele Passagen speichert der Pool pro Vorlage.
  - `passages_for` gibt wie bisher die ersten `MAX_PASSAGES` zurück.
  - Der Kopf der Beobachtung lautet „Pool dieser Vorlage: n Passagen; geprüft werden höchstens 10.“
  - `recheck_passages(self, vote_id: str, checked: List[Dict[str, Any]]) -> List[Dict[str, Any]]`: zuerst die gespeicherten Passagen, deren Schlüssel `(page_number, sha256(text))` nicht in `checked` vorkommt, in Fundreihenfolge; danach `checked`; insgesamt höchstens `MAX_PASSAGES`. Gibt es keine ungeprüfte Passage, ist das Ergebnis `[]`.
- Produces in `graph.py`:
  - `build_graph(..., *, budget=None, nachsuche: bool = True)` und `run_check(..., nachsuche: bool = True)`. Stufe 5 nutzt `False` für B2.
  - `AgentResult.checks: int = 0`
  - `NACHSUCHE_PROMPT` = „Ergebnis der Prüfung: im Dokument nicht belegt, kein Beleg gefunden. Nachsuche: Du hast genau einen weiteren Suchschritt. Rufe suche_passagen_vektor für die Vorlage {vote_id} mit einer anders formulierten Anfrage auf. Danach wird ein letztes Mal geprüft.“ Er wird als Nutzernachricht angehängt.
  - `NO_NEW_PASSAGES_NOTE` = „Nachsuche ohne neue Passagen; es bleibt beim Ergebnis der ersten Prüfung“
  - Ablauf:
    - `check_evidence` routet zu `nachsuche`, wenn das Ergebnis `nicht_belegt` ist (auch nach einem verworfenen Urteil), `checks < settings.MAX_CHECKS` gilt und `nachsuche` eingeschaltet ist; sonst zu `finish`.
    - `nachsuche` emittiert ein Ereignis, merkt sich die geprüften Passagen und das erste Ergebnis und setzt die Phase `"nachsuche"`. Danach geht es zu `decide`.
    - In der Phase `"nachsuche"` bietet `decide` nur `suche_passagen_vektor` an.
    - `act` verlangt eine gültige `suche_passagen_vektor`-Aktion für die geprüfte Vorlage. Alles andere endet als `technischer_fehler` mit „Nachsuche ohne gültigen Suchschritt: …“, ein Werkzeugfehler ebenso.
    - Liefert `recheck_passages` `[]`, endet der Lauf mit dem ersten Ergebnis und `note = NO_NEW_PASSAGES_NOTE`, ohne zweiten `verify`-Aufruf. Sonst folgt `verify` mit diesen Passagen.
    - Das Ergebnis der zweiten Prüfung ist endgültig.
  - Rekursionsgrenze: `2 * (max_steps + 2) + 9`.
- Produces in `decider.py`: Wird nur `suche_passagen_vektor` angeboten (Nachsuche), ruft `ScriptedDecider` es für die Vorlage der letzten Passagensuche mit der Behauptung als Anfrage auf. Sonst würde jeder Mock-Lauf mit „nicht belegt“ als `technischer_fehler` enden.

- [ ] **Step 1: Failing tests schreiben.** `FakeEngine(predictions=[...])` gibt die Vorhersagen der Reihe nach zurück. Für den Test mit vollem Pool gibt es eine Fake-Vorlage über die Seiten 1–30.

```python
# tests/test_agent_tools.py
def test_pool_stores_up_to_twice_the_check_size(self): ...  # 25 Passagen -> 20 gespeichert, passages_for -> 10
def test_recheck_puts_unchecked_passages_first(self): ...
def test_recheck_without_unchecked_passages_is_empty(self): ...
# tests/test_agent_graph.py
def test_nachsuche_runs_one_vector_search_and_a_second_check(self):
    # Preset: suche_vorlagen, suche_passagen(V1), pruefen(V1), suche_passagen_vektor(V1, 'Zustimmung schriftlich')
    # predictions: [Label 1, Label 0 mit gültigem Zitat]
    assert result.outcome == 'gestuetzt' and result.checks == 2 and len(engine.verify_calls) == 2
    assert decider.calls[-1]['tools'] == ['suche_passagen_vektor']
    assert 'kein Beleg gefunden' in decider.calls[-1]['messages'][-1]['content']
def test_pool_full_before_nachsuche_still_checks_new_passages(self):   # Review Focus 2
    # erste Suche 10 Passagen (alle geprüft), Nachsuche findet 3 neue:
    assert [p['page_number'] for p in engine.verify_calls[1]['passages'][:3]] == NEW_PAGES
    assert len(engine.verify_calls[1]['passages']) == 10
def test_second_nicht_belegt_is_final(self): ...             # 2 x Label 1 -> nicht_belegt, verify 2x, decide 4x
def test_nachsuche_without_new_passages_keeps_the_first_result(self): ...  # verify 1x, note == NO_NEW_PASSAGES_NOTE
def test_invalid_nachsuche_step_is_technical_error(self): ...   # pruefen oder ungültiges JSON in der Nachsuche
def test_nachsuche_in_another_vote_is_technical_error(self): ...
def test_budget_spent_in_nachsuche_is_technical_error(self): ...  # Entscheider wirft BudgetExceeded
def test_discarded_verdict_also_triggers_nachsuche(self): ...
def test_verdict_after_first_check_has_no_nachsuche(self): ...  # gestuetzt -> checks == 1
def test_nachsuche_can_be_switched_off(self): ...               # nachsuche=False -> nicht_belegt nach 1 Prüfung
# tests/test_agent_decider.py
def test_scripted_nachsuche_uses_the_vector_search(self): ...   # angeboten nur suche_passagen_vektor -> (Vorlage der letzten Suche, Behauptung)
# tests/test_agent_settings.py
def test_check_limits(self): ...                                # MAX_CHECKS == 2, MAX_RESEARCH_STEPS == 1
# tests/test_agent_cli.py
def test_format_steps_names_the_nachsuche(self): ...
```

  Die bestehenden Pool-Tests mit der Grenze 10 (`test_passages_are_pooled_deduplicated_and_capped`, `test_full_pool_says_so_and_drops_the_rest`) werden auf `POOL_LIMIT` umgestellt, ohne ihre Aussage abzuschwächen.
- [ ] **Step 2:** → FAIL.
- [ ] **Step 3: Implementieren.** Neue State-Felder sind `phase`, `checks`, `checked` und `first` (outcome, reasoning, note, evidence der ersten Prüfung).
- [ ] **Step 4:** → PASS, ganze Suite grün.
- [ ] **Step 5: Commit** `feat(agent): search once more after nicht_belegt and check a second and last time`

---

### Task 5: Eigener MCP-Server mit den drei Suchwerkzeugen

**Files:**
- Modify: `track_2a/requirements-agent.txt`, `track_2a/requirements-genai312-macos-arm64.lock.txt`
- Create: `track_2a/src/agent/mcp_server.py`
- Create: `track_2a/tests/fixtures/mcp_fake_server.py` (Testserver mit Fake-Backend über stdio; wird nicht als Test eingesammelt)
- Test: `track_2a/tests/test_agent_mcp_server.py`

**Interfaces:**
- Consumes: `SearchBackend`, `LocalBackend` (Task 1); `_TOOLS`, `validate_args`, `VOTE_CANDIDATES`, `VECTOR_PASSAGES` (tools.py); `settings.MAX_CLAIM_CHARS`; `text_utils.guess_language`
- Produces in `mcp_server.py`:

```python
SERVER_NAME = "fact-attack-suche"
VOTES_URI = "vorlagen://katalog"
SEARCH_TOOLS = ("suche_vorlagen", "suche_passagen", "suche_passagen_vektor")
INJECTED_ARGS = {"suche_passagen": ("behauptung",)}   # set by the graph, never by the model
def build_server(backend: SearchBackend) -> FastMCP
def serve(make_backend: Callable[[], SearchBackend]) -> None   # builds the backend with stdout redirected to stderr, then runs stdio
def make_local_backend(index_dir: Path) -> Tuple[LocalBackend, Callable[[], None]]  # SourceIndex + engine with ApertusClient(mock=True); closer
def server_environment(base: Mapping[str, str] = os.environ) -> Dict[str, str]
def server_command(index_dir: Path) -> List[str]   # [sys.executable, "-m", "src.agent.mcp_server", "--index-dir", str(index_dir)]
def main(argv: Optional[Sequence[str]] = None) -> int
```

- Werkzeuge des Servers. Namen und Beschreibungen kommen aus `_TOOLS`, die Parameterbeschreibungen aus denselben Pydantic-Feldern:

  | Werkzeug | Parameter | Ergebnis (JSON) |
  |---|---|---|
  | `suche_vorlagen` | `anfrage` | `{"candidates": [{vote_id, score, hits, best_page, snippet}]}` |
  | `suche_passagen` | `vorlage_id`, `behauptung` | `{"passages": [{page_number, text, section, …}]}` |
  | `suche_passagen_vektor` | `vorlage_id`, `anfrage` | `{"passages": [{page_number, text, section, …}]}` |

  - Die Ressource `vorlagen://katalog` (`application/json`) liefert `{"votes": [asdict(VoteRef), …]}`.
  - Der Server validiert selbst:
    - Modellargumente mit `validate_args`;
    - `behauptung` mit 1 bis `MAX_CLAIM_CHARS` Zeichen;
    - unbekannte Vorlagen-IDs werden mit einem Fehler abgelehnt.
  - Bei `suche_passagen` mit `guess_language(behauptung) != vote.language` meldet der Server den Fehler „Sprachübergreifende Suche braucht eine Übersetzung durch Apertus; der MCP-Server sendet keine Anfragen. Ohne MCP prüfen (--werkzeuge lokal).“
- `server_environment` übernimmt `PATH`, `HOME`, `LANG`, `HF_HOME`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `RAG_EMBEDDING_MODEL`, `RAG_MODEL_REVISION` und `AGENT_INDEX_DIR`, soweit gesetzt, und setzt `MOCK_APERTUS=1`. Nie übernommen werden `FACT_ATTACK_ENV_FILE`, `LLM_API_KEY`, `API_KEY`, `BASE_URL`, `LLM_BASE_URL` und `LANGSMITH_*`.
- Modul-Docstring: warum ein eigener Server. Für Schweizer Abstimmungsbüchlein ist kein passender Server bekannt. `mcp-server-qdrant` hat ein eigenes Embedding- und Payload-Schema, kennt weder die bewährte Suche noch die Bündelung pro Vorlage. Die Werkzeugfunktionen sind einmal implementiert (`LocalBackend`) und ohne MCP getestet.

- [ ] **Step 1: Pakete.** Ergänze in `requirements-agent.txt` den Kommentar `# Stage 3: own MCP server (FastMCP, stdio) and its binding into the graph.` sowie `mcp==1.30.0` und `langchain-mcp-adapters==0.3.2`. Erzeuge die Constraints-Datei aus den heutigen Pins neu, damit sich nichts Bestehendes bewegt, kompiliere das Lock neu und synchronisiere:

```bash
grep -E '^[A-Za-z0-9_.-]+==' track_2a/requirements-genai312-macos-arm64.lock.txt | sed 's/ \\$//' \
  > /tmp/fact-attack-genai312-constraints.txt
uv pip compile --python .venv312/bin/python --generate-hashes --constraint /tmp/fact-attack-genai312-constraints.txt \
  track_2a/requirements.txt track_2a/requirements-rag.txt track_2a/requirements-agent.txt \
  --output-file track_2a/requirements-genai312-macos-arm64.lock.txt
uv pip sync --python .venv312/bin/python --require-hashes track_2a/requirements-genai312-macos-arm64.lock.txt
```

  Expected:
  - Der Vergleich der `name==version`-Zeilen vor und nach dem Lauf zeigt nur hinzugekommene Pakete.
  - `.venv312/bin/python -c "import mcp, langchain_mcp_adapters"` endet mit Exit 0.

  Kollidiert ein Pin, wird nur dieser gelockert und in Task 9 dokumentiert. Löst eine Kursversion nicht auf, wird die nächste auflösbare gepinnt und ebenfalls dokumentiert.
- [ ] **Step 2: Failing tests schreiben.** Der Fixture-Server ruft `mcp_server.serve(make_fake_backend)` auf. Dabei ist `make_fake_backend` ein `LocalBackend` über einen Fake-Index (V1/V2 Mietrecht, Seiten (2, 3) und (4, 5)) und eine Fake-Engine. Die Fake-Engine liefert `[{"page_number": 2, "text": f"Treffer zu {claim}", "section": "COM"}]`. Schalter der Fixture (sie werden auch in Task 6 gebraucht):
  - `--laerm`: Der Aufbau schreibt „Lärm“ auf stdout.
  - `--leer`: `search_votes` liefert keine Kandidaten.
  - Eine `anfrage` „STIRB“ beendet den Prozess mit `os._exit(1)`.

  Die Tests verbinden sich über echtes stdio mit `mcp.ClientSession`, ein Server pro Testklasse (`setUpClass`).

```python
def test_server_lists_exactly_the_three_search_tools(self):
    assert [t.name for t in tools] == list(SEARCH_TOOLS)
    assert {t.name: t.description for t in tools} == {n: _TOOLS[n][1] for n in SEARCH_TOOLS}
    assert set(by_name['suche_passagen'].inputSchema['properties']) == {'vorlage_id', 'behauptung'}
def test_votes_resource_lists_every_vote(self): ...
def test_suche_vorlagen_round_trip(self): ...                    # JSON == LocalBackend.search_votes auf denselben Fakes
def test_suche_passagen_gets_the_claim(self): ...                # Text der Passage == 'Treffer zu <behauptung>'
def test_unknown_vote_id_is_an_error_result(self): ...           # isError, 'Unbekannte Vorlagen-ID'
def test_cross_lingual_claim_is_refused(self): ...               # Review Focus 5: frz. behauptung, dt. Vorlage -> isError, 'Übersetzung'
def test_stdout_noise_during_setup_does_not_break_the_protocol(self): ...  # Review Focus 4: Fixture mit --laerm
def test_server_environment_drops_secrets(self):
    env = server_environment({'FACT_ATTACK_ENV_FILE': 'x', 'LLM_API_KEY': 'k', 'API_KEY': 'k',
                              'LANGSMITH_API_KEY': 'k', 'HF_HOME': '/m', 'PATH': '/bin'})
    assert env['HF_HOME'] == '/m' and env['MOCK_APERTUS'] == '1'
    assert not {'FACT_ATTACK_ENV_FILE', 'LLM_API_KEY', 'API_KEY', 'LANGSMITH_API_KEY'} & set(env)
def test_local_backend_of_the_server_never_sends(self): ...      # make_local_backend mit gepatchtem SourceIndex: Engine-Client .mock is True
```

- [ ] **Step 3:** `../.venv312/bin/python -m pytest tests/test_agent_mcp_server.py -q` → FAIL.
- [ ] **Step 4: Implementieren.** API laut `mcp` 1.30.0: `FastMCP`, `add_tool`, `resource`, `run("stdio")`. `serve` baut das Backend unter `contextlib.redirect_stdout(sys.stderr)`. `main` schließt Engine und Index im `finally`. Weicht eine Signatur der installierten Version ab, gilt die installierte Version, und die Abweichung kommt in Task 9 in die Doku.
- [ ] **Step 5:** → PASS, ganze Suite grün. Ohne `mcp` wird die Testklasse übersprungen.
- [ ] **Step 6: Commit** `feat(agent): serve the search tools from an own FastMCP stdio server`

---

### Task 6: MCP-Anbindung – Client, Werkzeugschemas aus MCP, `--werkzeuge`

**Files:**
- Create: `track_2a/src/agent/mcp_client.py`
- Modify: `track_2a/src/agent/settings.py`
  - `TOOL_TRANSPORT` aus `AGENT_TOOL_TRANSPORT`, Standard `"lokal"`, erlaubt `("lokal", "mcp")`, sonst `ValueError` beim Import
  - `MCP_START_TIMEOUT_S = 120.0`, `MCP_CALL_TIMEOUT_S = 120.0`
- Modify: `track_2a/src/agent/graph.py` (`startup_failure`)
- Modify: `track_2a/src/agent/__main__.py` (`--werkzeuge`), `track_2a/src/agent/cli_format.py` (`NODE_LABELS["start"] = "Start"`)
- Test: `track_2a/tests/test_agent_mcp_client.py`, `test_agent_cli.py`, `test_agent_settings.py`

**Interfaces:**
- Consumes: `SERVER_NAME`, `VOTES_URI`, `SEARCH_TOOLS`, `INJECTED_ARGS`, `server_command`, `server_environment` (Task 5); `SearchBackend` (Task 1)
- Produces in `mcp_client.py`:

```python
class McpStartError(RuntimeError): ...   # German message, ends with the last 20 lines of the server's stderr
class McpBackend:                        # a SearchBackend over the own MCP server
    def __init__(self, command: Sequence[str], *, env: Mapping[str, str], cwd: Path,
                 start_timeout: float = settings.MCP_START_TIMEOUT_S,
                 call_timeout: float = settings.MCP_CALL_TIMEOUT_S): ...
    def start(self) -> None                 # McpStartError on any failure; the server process is ended then
    # votes, tool_specs(), search_votes(), proven_passages() (sends behauptung=claim), vector_passages(), close()
def model_spec(tool: "BaseTool") -> dict   # {"name","description","parameters"} without INJECTED_ARGS and without "title" keys
```

- Ablauf im Client:
  - Ein Hintergrund-Thread mit eigener asyncio-Schleife hält eine Besitzer-Coroutine. Sie betritt `stdio_client(StdioServerParameters(...), errlog=<Temp-Datei>)` und `ClientSession`, ruft `initialize()` auf und lädt die Werkzeuge mit `langchain_mcp_adapters.tools.load_mcp_tools(session)`. Danach liest sie `VOTES_URI` und meldet sich bereit.
  - Die Besitzer-Coroutine wartet auf das Schließsignal und verlässt die Kontexte in derselben Task (anyio verlangt das).
  - Aufrufe laufen über `asyncio.run_coroutine_threadsafe(tool.ainvoke(args), loop).result(call_timeout)`.
  - Ein Timeout, ein `isError` des Servers oder ein toter Prozess wird zu `RuntimeError` mit deutscher Meldung. `AgentTools.run` macht daraus einen Werkzeugfehler, und der Graph endet als `technischer_fehler`.
  - Das Ergebnis eines Werkzeugs ist JSON-Text oder eine Liste von Inhaltsblöcken mit `text`; beides wird zu `dict` dekodiert.
  - `close()` ist idempotent und wartet höchstens 10 s.
- `tool_specs()` liefert `model_spec(t)` für die drei geladenen MCP-Werkzeuge. Das ist der Weg „MCP → LangChain-Tool → Schema für `bind_tools`/JSON-Anweisung“.
- `graph.startup_failure(message: str, tracer: Tracer) -> AgentResult` emittiert `TraceEvent(step=0, node="start", summary="Werkzeuge nicht verfügbar", error=message)` und gibt `technischer_fehler` mit `TECHNICAL_REASON` zurück.
- CLI: `--werkzeuge lokal|mcp` (Standard `settings.TOOL_TRANSPORT`).
  - Bei `mcp` öffnet der Agentenprozess **keinen** `SourceIndex`, denn der lokale Qdrant-Index erlaubt nur einen Prozess. Die CLI baut `McpBackend(server_command(index_dir), env=server_environment(), cwd=config.BASE_DIR)`.
  - Ein `McpStartError` führt zu `startup_failure(f"MCP-Server nicht gestartet: {exc}", tracer)`, zur normalen Ergebnisausgabe und zu Exit 2. Es gibt keinen Wechsel auf lokal.
  - Die Engine für Prüfung und Belegkontrolle bleibt im Agentenprozess und zählt wie bisher auf dem Budget.

- [ ] **Step 1: Failing tests schreiben.** Sie nutzen den Fixture-Server aus Task 5 über echtes stdio, mit seinen Schaltern `--leer` und „STIRB“.

```python
def test_start_reads_votes_and_tool_specs(self): ...            # votes == Fake-Vorlagen; [s['name'] ...] == list(SEARCH_TOOLS)
def test_model_specs_match_the_local_specs(self):
    for mcp_spec, local in zip(backend.tool_specs(), LocalBackend(...).tool_specs()):
        assert (mcp_spec['name'], mcp_spec['description']) == (local['name'], local['description'])
        assert set(mcp_spec['parameters']['properties']) == set(local['parameters']['properties'])  # ohne behauptung
        assert sorted(mcp_spec['parameters'].get('required', [])) == sorted(local['parameters']['required'])
def test_round_trip_matches_the_local_backend(self): ...        # search_votes/proven_passages/vector_passages gleich
def test_failing_start_raises_with_the_server_stderr(self): ... # [python, -c, "import sys; sys.stderr.write('kaputt'); sys.exit(3)"]
def test_start_timeout(self): ...                               # Server schläft, start_timeout=1 -> McpStartError in < 10 s
def test_server_dying_mid_check_is_a_tool_failure_not_a_hang(self):  # Review Focus 3
    obs = tools.run('suche_vorlagen', {'anfrage': 'STIRB'})          # call_timeout=5
    assert obs.data['error_kind'] == ERROR_TOOL_FAILURE              # und run_check -> technischer_fehler
def test_close_is_idempotent_and_ends_the_server(self): ...
# tests/test_agent_cli.py
def test_mcp_start_failure_is_technical_error_without_fallback(self):
    # patch mcp_server.server_command -> fehlschlagender Befehl; patch __main__.SourceIndex -> AssertionError
    assert main(['Die Mietrechtsvorlage stärkt die Vermieter.', '--mock', '--werkzeuge', 'mcp', '--json']) == 2
    # stdout: outcome technischer_fehler, error enthält 'MCP-Server'
def test_mcp_mock_run_uses_the_server_tools(self): ...  # server_command -> Fixture mit --leer -> quelle_fehlt nach suche_vorlagen, Exit 0
# tests/test_agent_settings.py
def test_tool_transport_default_and_validation(self): ...       # 'lokal'; AGENT_TOOL_TRANSPORT=xml -> ValueError
```

- [ ] **Step 2:** `../.venv312/bin/python -m pytest tests/test_agent_mcp_client.py tests/test_agent_cli.py tests/test_agent_settings.py -q` → FAIL.
- [ ] **Step 3: Implementieren** wie in den Interfaces.
- [ ] **Step 4:** → PASS, ganze Suite grün.
- [ ] **Step 5: Mock-Lauf über MCP** mit echtem Index und echtem Server, ohne Netz:

```bash
cd track_2a && HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python -m src.agent "Wer in den Zivildienst wechselt, muss künftig mindestens 150 Diensttage leisten." \
  --mock --werkzeuge mcp
pgrep -f src.agent.mcp_server || echo "kein Serverprozess mehr"
```

  Expected:
  - Die Schrittliste zeigt `suche_vorlagen` mit `2026-06-14_de#2` auf Rang 1, dann `suche_passagen`, `pruefen`, Prüfung und Belegprüfung.
  - Exit 0, keine Netzanfrage.
  - Danach meldet `pgrep` keinen Serverprozess mehr.
- [ ] **Step 6: Commit** `feat(agent): bind the MCP search tools into the graph and choose the transport per run`

---

### Task 7: Natives Tool-Calling mit `tool_choice="required"` und `max_completion_tokens` (Smoke, kostenpflichtig)

> **Vor Step 5 Sheyla fragen**, ob der echte Lauf mit höchstens 4 Anfragen starten darf.

**Files:**
- Modify: `track_2a/src/agent/decider.py` (`NativeToolDecider` bindet mit `tool_choice="required"`)
- Create: `track_2a/scripts/agent_native_smoke.py`
- Create: `track_2a/results/genai/agent_native_smoke.json` und `agent_native_smoke.journal.jsonl` (Ergebnis des Laufs)
- Modify: `track_2a/src/agent/settings.py` (Standard von `TOOL_MODE` laut Regel, Kommentar mit Quelle)
- Test: `track_2a/tests/test_agent_decider.py`, `track_2a/tests/test_agent_native_smoke.py`

**Interfaces:**
- Consumes: `make_chat_model`, `NativeToolDecider`, `JsonActionDecider` (decider.py); `RequestBudget`, `budget_http_client(..., transport=...)` (budget.py); `TOOL_SPECS`; per Import aus `scripts/agent_tool_smoke.py`: `Situation`, `build_messages`, `_scrub`, `_is_fatal` (das Stufe-1-Skript bleibt unverändert)
- Produces:
  - `NativeToolDecider.decide` ruft `self.chat_model.bind_tools(tools, tool_choice="required")` auf.
  - Skript `agent_native_smoke.py` mit `--confirm-paid` und `--output` (Standard `results/genai/agent_native_smoke.json`). Es verweigert den Start ohne Bestätigung, ohne `FACT_ATTACK_ENV_FILE`, ohne Schlüssel und bei vorhandener Ergebnis- oder Journaldatei. Es nutzt ein einziges `RequestBudget(limit=4, journal_path=…)`.
  - Anfragen in dieser Reihenfolge:

    | Nr. | Modus | Situation | Zweck |
    |---|---|---|---|
    | 1 | JSON | a | erste echte Anfrage mit `max_completion_tokens` |
    | 2–4 | nativ, `required` | a, b, c | Tool-Calling |

  - Situationen und Erwartungen wie in Stufe 1: a `suche_vorlagen`, b `rueckfrage`, c `pruefen`. Die Beobachtungen werden einmal offline mit den Stufe-3-Werkzeugen erzeugt (echter Index, Mock-Engine, kein LLM) und als Konstanten eingetragen. Der Verlauf nutzt `suche_passagen` ohne `anfrage`.
  - Ein aufzeichnender Transport hält pro Anfrage fest: die sortierten obersten Schlüssel des JSON-Bodys, den Wert von `tool_choice`, den Wert von `max_completion_tokens` und den HTTP-Status. Nie gespeichert werden Body-Text, Nachrichten oder Header.
  - Lehnt der Endpunkt Anfrage 1 mit HTTP 400 ab, bricht das Skript ab; die übrigen Zeilen werden als `skipped` geführt.
  - `decide(rows) -> dict` nach der Regel, wörtlich im Report: „native wird Standard nur, wenn alle 3 nativen Anfragen mit tool_choice=required angenommen werden, 3 von 3 ein angebotenes Werkzeug mit gültigen Argumenten liefern und 3 von 3 das erwartete Werkzeug wählen; sonst bleibt json.“
  - `completion_cap_verdict(row) -> str`:
    - `"angenommen"` bei HTTP 200 und `tokens_completion <= settings.LLM_MAX_TOKENS`
    - `"abgelehnt"` bei HTTP 400
    - sonst `"unklar"`

- [ ] **Step 1: Failing tests schreiben**

```python
# tests/test_agent_decider.py
def test_native_decider_requires_a_tool_call(self): ...  # StubChat zeichnet bind_tools-kwargs auf: tool_choice == 'required'
def test_native_request_body_has_required_and_completion_cap(self):  # echtes ChatOpenAI über httpx.MockTransport
    assert body['tool_choice'] == 'required' and body['max_completion_tokens'] == settings.LLM_MAX_TOKENS
# tests/test_agent_native_smoke.py
def test_refuses_without_confirmation_env_key_or_with_existing_output(self): ...  # nichts gebaut, Exit 1
def test_rule_needs_three_accepted_valid_expected_native_runs(self): ...  # 3/3/3 -> native; 2 von 3 erwartet -> json; ein 400 -> json
def test_completion_cap_verdict(self): ...               # 200 + 900 Tokens -> angenommen; 400 -> abgelehnt; Ausnahme -> unklar
def test_recorder_keeps_keys_not_values(self): ...       # Aufzeichnung enthält 'messages' als Schlüssel, nicht den Behauptungstext
def test_four_requests_with_mock_transport(self): ...    # MockTransport: genau 4 Journalzeilen, Reihenfolge json, native x3
```

- [ ] **Step 2:** → FAIL. **Step 3:** implementieren. **Step 4:** → PASS, ganze Suite grün.
- [ ] **Step 5: Echter Lauf** (erst nach Sheylas OK):

```bash
cd track_2a && FACT_ATTACK_ENV_FILE=/Users/sheyla/Projects/Apertus-Team-Fact-Attack-2026/track_2a/.env \
  ../.venv312/bin/python scripts/agent_native_smoke.py --confirm-paid
```

  Expected: höchstens 4 Journalzeilen; der Report enthält die Regel, die Entscheidung und das Urteil zu `max_completion_tokens`.
- [ ] **Step 6: Folgen umsetzen.**
  - `TOOL_MODE` laut Entscheidung setzen, mit Kommentar und Verweis auf den Report, und `test_defaults` anpassen.
  - Lautet das Urteil zu `max_completion_tokens` „abgelehnt“, sendet `make_chat_model` die Obergrenze als `extra_body={"max_tokens": settings.LLM_MAX_TOKENS}` statt `max_tokens=`. Dazu kommt der Test `test_make_chat_model_caps_with_max_tokens`.
  - **Kein** zweiter bezahlter Lauf ohne neues OK.
- [ ] **Step 7: Commit** `test(agent): record a capped native tool-calling smoke with tool_choice=required and the completion cap`

---

### Task 8: Schwellen des Wächters auf Dev kalibrieren (erst nach dem Sperren)

> Startet erst, wenn `load_testset("dev")` ohne Fehler lädt (Stufe 2, Task 9). Vorher `genai/agent-testset` in den Branch mergen. Ohne Apertus und ohne LLM; nur der lokale Index.

**Files:**
- Create: `track_2a/scripts/calibrate_guard.py`
- Create: `track_2a/results/genai/agent_guard_calibration.json`
- Modify: `track_2a/src/agent/settings.py` (`GUARD_SCORE_MARGIN`, `GUARD_MIN_HITS_RATIO`; Kommentar mit Verweis auf den Report)
- Test: `track_2a/tests/test_agent_guard_calibration.py`

**Interfaces:**
- Consumes: `testset.load_testset`, `EvalCase` (Stufe 2); `guard.assess`, `Thresholds` (Task 2); `SourceIndex.search_votes`
- Produces in `calibrate_guard.py`:

```python
GRID_MARGINS = (0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.045, 0.05)
GRID_HITS_RATIOS = (0.0, 0.25, 0.5, 0.75)
def calibration_rows(cases: List[EvalCase], search: Callable[[str], List[VoteCandidate]]) -> List[dict]
    # one row per case with vote_id or kind in (mehrdeutig, ohne_abstimmung):
    # {id, kind, gold, ambiguous_with, candidates: [{vote_id, score, hits}]}; search = index.search_votes(claim, k=5)
def evaluate(rows: List[dict], thresholds: Thresholds) -> dict
    # missed_ambiguous: mehrdeutig case not assessed mehrdeutig, or {gold} | ambiguous_with not inside close
    # false_confidence: case with a gold vote (not mehrdeutig) assessed eindeutig with top != gold
    # unnecessary_questions: same cases assessed mehrdeutig with top == gold
def choose(rows: List[dict]) -> dict
    # minimum of (missed_ambiguous, false_confidence, unnecessary_questions, score_margin, min_hits_ratio)
    # over the grid; none_below stays None
def main(argv=None) -> int   # load_testset("dev") only, never allow_test; writes the report
```

  Report:
  - `rows`, die Tabelle aller Gitterpunkte, `chosen`, die Regel als Text, die vorläufigen Werte
  - `testset_cases_sha256` aus dem Manifest, Datum
  - die Begründung, warum `none_below` nicht kalibriert wird (1 Dev-Fall `ohne_abstimmung`; dessen Top-Score steht informativ dabei)

- [ ] **Step 1: Failing tests schreiben**

```python
def test_evaluate_counts_the_three_error_kinds(): ...        # je eine Fake-Zeile pro Fehlerart
def test_choose_prefers_catching_ambiguity_then_avoiding_false_confidence(): ...
def test_choose_tie_takes_the_smallest_values(): ...
def test_main_reads_only_the_dev_split():   # load_testset gepatcht: einziger Aufruf ('dev',), ohne allow_test
def test_settings_match_the_calibration_report(): ...        # skip, solange der Report fehlt
```

- [ ] **Step 2:** → FAIL. **Step 3:** implementieren. **Step 4:** → PASS.
- [ ] **Step 5: Kalibrieren**

```bash
cd track_2a && HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python scripts/calibrate_guard.py
```

  Expected: Der Report liegt vor, `chosen.missed_ambiguous == 0`. Ist das auf keinem Gitterpunkt erreichbar, wird das so dokumentiert. Das Gitter wird nicht nachträglich erweitert, um ein Dev-Ergebnis zu erzwingen.
- [ ] **Step 6:** Die Werte in `settings.py` eintragen; die ganze Suite ist grün. Danach werden die Schwellen bis zum Konfigurationsfreeze (Stufe 5) nicht mehr verändert.
- [ ] **Step 7: Commit** `feat(agent): calibrate the guard thresholds on the dev split of the locked testset`

---

### Task 9: Echte Läufe und Doku

> **Vor Step 1 Sheyla fragen:** 5 Läufe mit je höchstens 12 Anfragen, also höchstens 60 Anfragen.

**Files:**
- Create: `docs/genai/AGENT_STUFE3.md`
- Create: `track_2a/results/genai/agent_stage3_runs.json`, Protokolle unter `track_2a/results/genai/agent_stage3_traces/<Nr>_<Name>/`
- Modify: `docs/genai/VALIDIERUNG.md` (Abschnitt „Agentenschicht Stufe 3“ mit Testzahl)

- [ ] **Step 1: Echte Läufe** über die CLI mit `--json --max-requests 12 --trace-dir results/genai/agent_stage3_traces/<Nr>_<Name>`. Die Erwartung jedes Laufs steht vor dem Lauf in `agent_stage3_runs.json`. Kein Lauf wird wiederholt.

  | # | Lauf | Erwartung |
  |---|---|---|
  | 1 | „Die Mietrechtsvorlage stärkt die Vermieter.“ | `rueckfrage`, `asked_by == "guard"`, ohne Prüfung |
  | 2 | dieselbe Behauptung mit `--quelle 2024-11-24_de#2` (die Antwort) | ein Urteil zur Untermiete |
  | 3 | „Die Schweiz führt den Euro ein.“ | `suche_vorlagen` vor `quelle_fehlt` |
  | 4 | Zivildienst-Behauptung aus Stufe 1 mit `--werkzeuge mcp` | `gestuetzt`; MCP mit echtem Apertus |
  | 5 | eine offline aus `2026-06-14_de` gewählte Behauptung zur Zivildienst-Vorlage, die das Büchlein nicht entscheidet | `nicht_belegt` nach Nachsuche, `checks == 2` |

  Für Lauf 5 werden Behauptung, Seite und Begründung vorher notiert. Es darf kein Fall aus `cases.jsonl` sein.
- [ ] **Step 2: `AGENT_STUFE3.md` schreiben:**
  - Was gebaut ist; Graphskizze mit `ask` und `nachsuche`.
  - Wächter: Regel, Schwellen (kalibriert oder vorläufig, mit Report), warum die Behauptung und nicht die Anfrage.
  - Rückfrage beantworten: `--quelle` als neue Prüfung mit eigenem Deckel; Ausblick auf `interrupt` in Stufe 4.
  - Regel für `quelle_fehlt`.
  - Nachsuche und Budget: Tabelle 5 / 1 / 2 / 12, was bei jeder Grenze passiert.
  - Entscheidung „Behauptung statt Anfrage“ für die bewährte Suche; Pool nur aus den Seiten der Vorlage.
  - MCP:
    - Aufbau, Start, Fehlerverhalten;
    - Grenze: keine sprachübergreifende bewährte Suche über MCP;
    - warum ein eigener Server: Suche nach einem passenden Server für Abstimmungsbüchlein mit Datum und Ergebnis notieren; `mcp-server-qdrant` und warum er nicht passt;
    - wie Stufe 4 die synchrone Fassade `McpBackend` in Streamlit nutzen kann.
  - Paketversionen inklusive Abweichungen von den Kursversionen und gelockerter Pins.
  - Ergebnis des nativen Smoke und `max_completion_tokens`.
  - Die fünf Läufe mit gekürzten Schrittlisten, Budgetnachweis aus den Journalen (Zeilen pro Lauf, Summe).
  - Grenzen:
    - zusammengesetzte Behauptungen erst in Stufe 5;
    - keine „keine“-Schwelle;
    - Einzelläufe sind keine Messung.
- [ ] **Step 3:** Ganze Suite grün; die Anzahl kommt in `VALIDIERUNG.md`.
- [ ] **Step 4: Commit** `docs(agent): document stage 3 guard, nachsuche, MCP and capped real runs`
