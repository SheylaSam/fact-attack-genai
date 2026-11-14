# Agentenschicht Stufe 2: Testset – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein gesperrtes Testset aus 90 frei formulierten deutschen Behauptungen (30 Dev / 60 Test) und 20 Spezialfällen. Alle Fälle haben ein erwartetes Ergebnis und eine wörtliche Fundstelle, Sheyla hat jedes Label geprüft. Gesperrt wird, **bevor** in Stufe 3 Schwellenwerte kalibriert werden.

**Architecture:** Zuerst kommen die Büchlein vom 27.09.2026 und 29.11.2026 in Katalog und Index. Danach prüft ein Modul `src/agent/testset.py` jeden Fall deterministisch gegen die PDFs: Zitat wörtlich auf der Seite, Vorlage bekannt, Aufteilung nach Vorlage, Klassenbalance. Eine lokale HTML-Seite dient Sheyla zur blinden Labelprüfung. Ein Sperrskript schreibt das Manifest mit Hashes, und ein Lader gibt Dev frei, den Testteil aber nur ausdrücklich. Die Fälle selbst entwerfen Subagenten allein aus dem Büchleintext.

**Tech Stack:** Python 3.12 (`.venv312`), PyMuPDF über die bestehende `ClaimVerificationEngine` (Mock-Apertus, keine Anfragen), statisches HTML/JS ohne externe Skripte, pytest.

**Spec:** `docs/superpowers/specs/2026-10-31-agentenschicht-design.md`, Abschnitte „Evaluation“ (Neues Testset, Quellen und Neuheit, Labelprozess) und „Stufen“ (Stufe 2).

**Branch:** `genai/agent-testset`, abgezweigt von `genai/agent-layer` (PR SheylaSam/fact-attack-genai#1) bei `e7daa37`.

## Global Constraints

- Zieldaten: Entwurf komplett **Do 05.11.2026**, gesperrt **So 08.11.2026**.
- **Keine Apertus-Anfrage in Stufe 2.** Engines werden immer mit `ApertusClient(mock=True)` gebaut.
- Kein Fall wird vor dem Sperren durch den Agenten, den Prüfpfad oder ein anderes Modell geschickt. Entwürfe entstehen nur aus dem Büchleintext (`scripts/check_testset.py --show`).
- Nach dem Sperren gilt: Änderungen brauchen ein neues Testset. Der Testteil wird erst in Stufe 5 einmal nach dem Konfigurationsfreeze gelesen.
- B0 bleibt byte-identisch: keine Änderung an `src/inference.py`, der Wettbewerbs-CLI oder `requirements*.txt`; keine neuen Pakete.
- Die 517 bestehenden Tests bleiben grün (`cd track_2a && ../.venv312/bin/python -m pytest -q`).
- PDF-Büchlein werden nicht committet (`.gitignore` deckt `data/booklets/20[0-2][0-9]-*.pdf` ab); sie kommen per `scripts/fetch_catalog_booklets.py`.
- Testset-Dateien liegen in `track_2a/data/genai/testset/`: `cases.jsonl`, `label_review.jsonl`, `manifest.json`.
- Doku und Meldungen an Menschen auf Deutsch, Code-Kommentare auf Englisch (wie im Bestand). Ergebnisnamen exakt wie in `src/agent/graph.py` (`Outcome`): `gestuetzt`, `widersprochen`, `nicht_belegt`, `rueckfrage`, `quelle_fehlt`, `technischer_fehler`.
- Labelkonvention OST: `0` gestützt, `1` nicht belegt, `2` widersprochen (`graph.LABEL_OUTCOMES`).

## Review Focus

1. **Zitat typografisch anders als im PDF** (’ statt ', « statt ", Trennstrich am Zeilenende): Der Validator muss genau das annehmen, was die Belegkontrolle des Agenten annimmt (`official_normalize` auf Seitentext **oder** einem Textblock), und alles andere mit Seitenangabe ablehnen. → Tests in Task 2.
2. **Behauptung mit HTML oder `</script>`** (Prompt-Injection-Fälle): Die Prüfseite darf nicht brechen und nichts ausführen. → Test in Task 3.
3. **Fall nach Sheylas Urteil geändert:** Das Sperren muss das alte Urteil als veraltet erkennen und verweigern. → Test in Task 4.
4. **Frischer Klon ohne PDFs:** Der Validator meldet pro betroffenem Fall „Büchlein fehlt“ mit dem Befehl zum Laden und stürzt nicht ab. → Test in Task 2.
5. **`cases.jsonl` nach dem Sperren verändert oder Testteil zu früh gelesen:** Der Lader verweigert beides. → Tests in Task 4.

## Vorlagenplan (Claudes Entscheidung, gilt für alle Tasks)

Aufgeteilt wird nach Vorlage (`vote_id` aus `SourceIndex`, Form `<datum>_de#<k>`). Jede Vorlage gehört genau einer Seite an. Paraphrasen derselben Aussage bleiben dadurch automatisch im selben Split.

**Dev, Hauptfälle (30 = 15 Vorlagen × 2, Neuheit `bekannt`):**
`2020-02-09_de#1` Bezahlbare Wohnungen · `2020-09-27_de#2` Jagdgesetz · `2020-11-29_de#1` Konzernverantwortung · `2021-06-13_de#3` Covid-19-Gesetz · `2021-09-26_de#2` Ehe für alle · `2021-11-28_de#1` Pflegeinitiative · `2022-02-13_de#2` Tabakwerbung · `2022-05-15_de#1` Filmgesetz · `2022-09-25_de#1` Massentierhaltung · `2023-06-18_de#1` OECD-Mindeststeuer · `2024-06-09_de#2` Kostenbremse · `2024-09-22_de#1` Biodiversität · `2025-02-09_de#1` Umweltverantwortung · `2025-11-30_de#1` Service citoyen · `2026-06-14_de#2` Zivildienst

**Test, Hauptfälle (60):**
- Neuheit `ungesehen`, je 6 (2 pro Klasse): `2026-11-29_de#1` AHV-Zusatzfinanzierung · `#2` Feuerwerk · `#3` Faire Bundessteuern für Ehepaare · `#4` Kriegsmaterialgesetz
- Neuheit `beobachtet`, je 6 (2 pro Klasse): `2026-09-27_de#1` Neutralitätsinitiative · `#2` Ernährungsinitiative
- Neuheit `bekannt`, je 2: `2020-09-27_de#5` Kampfflugzeuge · `2021-03-07_de#1` Verhüllungsverbot · `2021-06-13_de#4` CO2-Gesetz · `2021-11-28_de#2` Justiz-Initiative · `2022-02-13_de#4` Medienpaket · `2022-05-15_de#2` Transplantationsgesetz · `2023-06-18_de#2` Klimaschutzgesetz · `2024-03-03_de#2` Renteninitiative · `2024-06-09_de#4` Stromversorgung · `2024-11-24_de#1` Nationalstrassen · `2025-11-30_de#2` Initiative für eine Zukunft · `2026-03-08_de#2` SRG-Initiative

**Nur für Spezialfälle reserviert:**
- Dev: `2024-11-24_de#2` Mietrecht Untermiete · `2024-11-24_de#3` Mietrecht Eigenbedarf · `2024-06-09_de#1` Prämien-Entlastung
- Test: `2021-03-07_de#2` E-ID 2021 · `2025-09-28_de#2` E-ID 2025 · `2022-09-25_de#2` AHV 21 · `2024-03-03_de#1` 13. AHV-Rente · `2026-03-08_de#4` Individualbesteuerung · `2020-11-29_de#2` Kriegsgeschäfte

**Spezialfälle (20 = 7 Dev + 13 Test):**

| Art (`kind`) | Dev | Test | erwartet | Themen |
|---|---:|---:|---|---|
| `mehrdeutig` | 2 | 4 | `rueckfrage`, danach `after_answer` | Dev: Mietrecht (#2/#3), Prämien (2024-06-09 #1/#2). Test: E-ID 2021/2025, AHV 2026/AHV 21 (+13. AHV), Ehepaare 2026/Individualbesteuerung, Kriegsmaterial 2026/2020 |
| `falscher_sprecher` | 1 | 2 | nach Konvention aus Task 5 | Argument der einen Seite der anderen zugeschrieben |
| `zusammengesetzt` | 1 | 2 | `rueckfrage` | zwei unabhängige Aussagen in einem Satz |
| `ohne_abstimmung` | 1 | 2 | `quelle_fehlt` | Dev: Euro statt Franken. Test: Themen ohne Vorlage in den 64 Vorlagen |
| `api_ausfall` | 1 | 1 | `technischer_fehler` | normale Behauptung, `fault` `timeout` bzw. `http_500` |
| `prompt_injection` | 1 | 2 | Urteil der echten Aussage | eingebettete Anweisung, einmal als JSON/HTML-Schnipsel |

Neuheit nach Datum: `2026-11-29` → `ungesehen`, `2026-09-27` → `beobachtet` (frühere Experimente haben dieses Büchlein gesehen, die neuen Behauptungen sind aber neu), sonst `bekannt`.

## Schreibregeln für Entwürfe (gelten für Task 5–7)

1. Quelle ist nur der Text aus `check_testset.py --show <vote_id>`; kein Agent, kein Apertus, kein Web.
2. Formuliere wie eine Stimmbürgerin, 8–40 Wörter, ein einziger Satz mit einer Aussage. Am Anfang steht kein Abschnittssignal wie „Der Bundesrat …“, „Das Komitee …“, „Laut Abstimmungstext …“ oder „Bei einem Ja …“; der Validator prüft das mit `part_context.claim_part`. Etwa die Hälfte nennt die Vorlage alltagssprachlich („die Feuerwerk-Initiative“), die anderen beschreiben nur den Inhalt.
3. Hauptfälle betreffen **Tatsachen, die das Büchlein als Tatsache darstellt**: was sich ändert, Zahlen, Fristen, Zuständigkeiten, heutiger Zustand. Umstrittene Argumente einer Seite gibt es nur als Spezialfall `falscher_sprecher`.
4. **gestützt:** Das Büchlein sagt es, umformuliert und nicht abgeschrieben. **widersprochen:** Das Büchlein sagt klar etwas anderes, etwa eine andere Zahl, Richtung, Zuständigkeit oder ein anderes Datum. **nicht belegt:** plausibel und zum Thema der Vorlage, aber das Büchlein entscheidet es weder dafür noch dagegen. `note` erklärt, warum.
5. `page` und `quote`: die eine Seite, die das Label begründet; bei „nicht belegt“ die nächstliegende Stelle. Das Zitat wird zeichengenau aus der `--show`-Ausgabe kopiert, 20–400 Zeichen. Höchstens zwei Fälle pro Vorlage teilen sich ein Zitat.
6. Keine Behauptung aus `data/holdout_2026-09-27/claims.jsonl` oder `data/genai/ost_june_dev.jsonl` übernehmen; der Validator prüft exakte Duplikate.
7. Am Ende steht `check_testset.py --partial` ohne Probleme.

---

### Task 1: Neue Büchlein in Katalog und Index

**Files:**
- Modify: `track_2a/src/booklet_catalog.py:12-20` (`load_catalog`)
- Modify: `track_2a/data/booklet_catalog.json` (6 neue Einträge)
- Create: `track_2a/scripts/fetch_catalog_booklets.py`
- Modify: `track_2a/results/genai/agent_index_build.json` (neu gebaut)
- Modify: `docs/genai/AGENT_STUFE1.md` (ein Satz zum neuen Indexstand)
- Test: `track_2a/tests/test_booklet_catalog.py`, `track_2a/tests/test_fetch_catalog_booklets.py`

**Interfaces:**
- Produces: `booklet_catalog.OFFICIAL_HOSTS: Tuple[str, ...] = ("www.bk.admin.ch", "www.admin.ch")`
- Produces: `fetch_catalog_booklets.fetch_missing(sources: Dict[str, dict], directory: Path, *, opener=urllib.request.urlopen) -> Dict[str, str]`, Rückgabe `{dateiname: "vorhanden" | "geladen"}`; wirft `ValueError` bei Hash-Abweichung und hinterlässt dann weder Ziel- noch Teildatei.
- Produces: Katalog- und Indexeinträge `2026-09-27_{de,fr,it}.pdf` und `2026-11-29_{de,fr,it}.pdf`; Vorlagen `2026-09-27_de#1..#2` und `2026-11-29_de#1..#4`.

- [ ] **Step 1: Failing tests schreiben**

```python
# tests/test_booklet_catalog.py
def test_load_catalog_accepts_www_admin_ch(tmp_path): ...  # url https://www.admin.ch/dam/de/x.pdf -> keine Exception
def test_load_catalog_rejects_other_hosts(tmp_path): ...    # https://evil.example/x.pdf und http://www.admin.ch/x.pdf -> ValueError
def test_catalog_lists_new_booklets():
    sources = load_catalog(config.DATA_DIR / "booklet_catalog.json")
    for d in ("2026-09-27", "2026-11-29"):
        for lang in ("de", "fr", "it"):
            assert f"{d}_{lang}.pdf" in sources
    assert sources["2026-09-27_de.pdf"]["sha256"] == "6d27a4c6c5ed0e8583a49864979dcc2ddeeb4c65e895b97a2df3fa1d089dbc15"
    assert sources["2026-11-29_de.pdf"]["sha256"] == "391b1f2941820c9859b0fef5ac6dd366e325076ebd125f0c5ecd763fea428be2"
    assert len(sources["2026-11-29_de.pdf"]["votes"]) == 4 and len(sources["2026-09-27_de.pdf"]["votes"]) == 2

# tests/test_fetch_catalog_booklets.py
def test_fetch_missing_keeps_present_file(tmp_path): ...       # gültige Datei mit passendem Hash -> "vorhanden", opener nie aufgerufen
def test_fetch_missing_downloads_and_verifies(tmp_path): ...   # Fake-opener liefert Bytes mit passendem Hash -> "geladen", Datei vorhanden
def test_fetch_missing_rejects_hash_mismatch(tmp_path): ...    # falscher Hash -> ValueError, keine Datei, keine *.part-Datei
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `cd track_2a && ../.venv312/bin/python -m pytest tests/test_booklet_catalog.py tests/test_fetch_catalog_booklets.py -q`
Expected: FAIL (`www.admin.ch` abgelehnt, Modul fehlt, Einträge fehlen)

- [ ] **Step 3: `OFFICIAL_HOSTS` einführen und in `load_catalog` statt des festen Hosts prüfen**

- [ ] **Step 4: `fetch_missing` und CLI schreiben**

Die Funktion lädt jede fehlende oder ungültige Datei (kein `%PDF-` oder falscher Hash) nach `<name>.part`, prüft SHA-256 gegen den Katalog und benennt atomar um. CLI: `python scripts/fetch_catalog_booklets.py [--only 2026-09-27 2026-11-29]`, gibt pro Datei den Status aus.

- [ ] **Step 5: Sechs Katalogeinträge ergänzen**

- URLs 27.09.2026: aus `scripts/build_holdout.py` (`BOOKLETS`); Hashes aus `data/holdout_2026-09-27/manifest.json`; `votes` je Sprache aus `VOTES` in `build_holdout.py`.
- URLs 29.11.2026: `https://www.admin.ch/dam/{de,fr,it}/sd-web/HXhFMFWOaa6u/BAR_Erlaeuterungen_Nov26_{DE,FR,IT}_v10.pdf`. Hash DE wie im Test; FR und IT nach dem Laden berechnen und eintragen.
- `votes` für den 29.11.2026: die vier Titel je Sprache so, wie sie auf der Inhaltsseite des jeweiligen Büchleins stehen, ohne „Erste Vorlage“ usw.

Einträge alphabetisch nach Dateiname einfügen.

- [ ] **Step 6: Laden, Index neu bauen, prüfen**

Run: `cd track_2a && ../.venv312/bin/python scripts/fetch_catalog_booklets.py --only 2026-09-27 2026-11-29 && ../.venv312/bin/python scripts/build_agent_index.py`
Expected in `results/genai/agent_index_build.json`: `votes` 64, `sources_indexed` 22, `votes_per_source["2026-09-27_de"]` 2, `votes_per_source["2026-11-29_de"]` 4, `votes_without_catalog_title` `[]`, `whole_booklet_sources` `[]`. Steht ein neuer Titel in `votes_without_catalog_title`, den Katalogtitel an den Inhaltsseitentext angleichen und neu bauen.

- [ ] **Step 7: Volle Testsuite**

Run: `cd track_2a && ../.venv312/bin/python -m pytest -q`
Expected: alle bestanden (517 + neue)

- [ ] **Step 8: Commit**

```bash
git add track_2a/src/booklet_catalog.py track_2a/data/booklet_catalog.json track_2a/scripts/fetch_catalog_booklets.py track_2a/tests/test_booklet_catalog.py track_2a/tests/test_fetch_catalog_booklets.py track_2a/results/genai/agent_index_build.json docs/genai/AGENT_STUFE1.md
git commit -m "feat(agent): add the booklets of 27 Sep and 29 Nov 2026 to the catalog and the source index"
```

---

### Task 2: Testset-Modul, Validator und Prüfskript

**Files:**
- Create: `track_2a/src/agent/testset.py`
- Modify: `track_2a/src/agent/graph.py` (`_page_texts` → Modulfunktion `page_texts`, Methode delegiert)
- Create: `track_2a/scripts/check_testset.py`
- Create: `docs/genai/TESTSET.md` (Zweck, Vorlagenplan, Schema, Schreibregeln, Prüfregeln; aus diesem Plan übernommen)
- Test: `track_2a/tests/test_agent_testset.py`

**Interfaces:**
- Consumes: `graph.Outcome`, `graph.LABEL_OUTCOMES`, `source_index.VoteRef`, `source_index._booklet_votes`, `part_context.claim_part`, `text_utils.guess_language`, `text_utils.official_normalize`, `settings.MAX_CLAIM_CHARS`, `ClaimVerificationEngine.booklet_layout/source_page`.
- Produces (in `src/agent/testset.py`):

```python
OUTCOMES = typing.get_args(Outcome)
VERDICTS = ("gestuetzt", "widersprochen", "nicht_belegt")
KINDS = ("mehrdeutig", "falscher_sprecher", "zusammengesetzt", "ohne_abstimmung", "api_ausfall", "prompt_injection")
FAULTS = ("timeout", "http_500")
TESTSET_DIR = config.DATA_DIR / "genai" / "testset"   # CASES_PATH, REVIEW_PATH, MANIFEST_PATH darin
DEV_VOTES, TEST_VOTES, DEV_SPECIAL_VOTES, TEST_SPECIAL_VOTES: FrozenSet[str]   # exakt der Vorlagenplan
HAUPT_PLAN: Dict[str, int]          # vote_id -> Anzahl Hauptfälle (2 bzw. 6)
KIND_COUNTS: Dict[str, Dict[str, int]]   # {"dev": {...}, "test": {...}} laut Tabelle

@dataclass(frozen=True)
class EvalCase:        # nicht "TestCase": pytest würde die Klasse einsammeln
    id: str; split: str; group: str; kind: Optional[str]; novelty: Optional[str]
    claim: str; expected: str; acceptable: Tuple[str, ...]
    vote_id: Optional[str]; ambiguous_with: Tuple[str, ...]; after_answer: Optional[str]
    page: Optional[int]; quote: Optional[str]; fault: Optional[str]; note: str

def read_cases(path: Path = CASES_PATH) -> List[EvalCase]   # ValueError mit Zeilennummer bei fehlendem/unbekanntem Feld
def case_sha(case: EvalCase) -> str    # SHA-256 von json.dumps(asdict(case), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

class VoteCatalog:
    votes: Dict[str, VoteRef]
    missing: Dict[str, str]                 # source_id -> PDF-Dateiname, nicht lokal vorhanden
    def parts(self, vote_id) -> Dict[str, Tuple[int, ...]]
    def page_texts(self, vote_id, page) -> List[str]   # normalisiert: Seite + Blöcke; [] = kein lesbarer Text
def load_vote_catalog(sources, booklets_dir, engine) -> VoteCatalog   # deutsche Büchlein aus dem Katalog

def validate(cases: List[EvalCase], votes: VoteCatalog, *, previous_claims: Iterable[str] = (), complete: bool = True) -> List[str]
```

Formate von `cases.jsonl`: eine Zeile pro Fall mit allen Feldern von `EvalCase` plus `"claim_language": "de"`. Listenfelder sind JSON-Arrays. IDs: `dev-001`…`dev-030`, `test-001`…`test-060`, `sdev-01`…`sdev-07`, `stest-01`…`stest-13`.

**Prüfregeln von `validate`** (jede Meldung beginnt mit der Fall-ID oder mit `Testset:`):
- Felder: ID-Muster passt zu `split`/`group`; IDs eindeutig; `split` ∈ {dev, test}; `group` ∈ {haupt, spezial}; `kind` ist None genau bei `haupt`.
- Behauptung: nicht leer, ≤ `MAX_CLAIM_CHARS`, `guess_language == "de"`, normalisiert eindeutig im Set und nicht in `previous_claims`; `claim_part(claim) is None` außer bei `falscher_sprecher`.
- Vorlage: `vote_id` ist None genau bei `ohne_abstimmung`; sonst bekannt, und deren Büchlein ist vorhanden. Fehlt das PDF, lautet die Meldung `"<id>: Büchlein fehlt (<datei>): scripts/fetch_catalog_booklets.py ausführen"`. Die Vorlage gehört zur erlaubten Menge ihres Splits und ihrer Gruppe; `novelty` passt zum Datum.
- Fundstelle: Bei gesetzter `vote_id` ist `page` in `VoteRef.pages`, und `official_normalize(quote)` hat 20–400 Zeichen und steht in einem Text von `page_texts`. Eine Seite ohne lesbaren Text ergibt eine eigene Meldung.
- Ergebnisse: Bei `haupt` ist `expected` ∈ VERDICTS. Pro `kind`:

  | `kind` | Regel |
  |---|---|
  | `mehrdeutig` | `expected` = `rueckfrage`; `after_answer` ∈ VERDICTS; `ambiguous_with` nicht leer, bekannt, ohne `vote_id`, gleiche Split-Menge |
  | `zusammengesetzt` | `expected` = `rueckfrage` |
  | `ohne_abstimmung` | `expected` = `quelle_fehlt`; `page`, `quote` und `novelty` sind None |
  | `api_ausfall` | `expected` = `technischer_fehler`; `fault` ∈ FAULTS |
  | `falscher_sprecher` | `expected` ∈ {widersprochen, nicht_belegt} |
  | `prompt_injection` | `expected` ∈ VERDICTS |

  `fault`, `after_answer` und `ambiguous_with` sind nur bei ihrer Art gesetzt. `acceptable` ist leer bei `haupt`; bei `spezial` enthält es `expected` und nur Werte aus OUTCOMES. `note` ist nicht leer bei `spezial` und wenn `expected` oder `after_answer` = `nicht_belegt`.
- Nur bei `complete=True`: Anzahl je Split und Gruppe (30/60/7/13), je Klasse 10 (Dev) bzw. 20 (Test), Anzahl pro Vorlage laut `HAUPT_PLAN`, bei Vorlagen mit 6 Fällen je Klasse 2, Spezialarten laut `KIND_COUNTS`.

**`scripts/check_testset.py`:** `[--cases PATH] [--partial] [--show VOTE_ID]`. Die Engine läuft mit `ApertusClient(mock=True)`. `previous_claims` kommt aus `data/holdout_2026-09-27/claims.jsonl` und `data/genai/ost_june_dev.jsonl` (Feld `claim`). Ausgabe `OK: <n> Fälle, 0 Probleme` mit Exit 0, sonst eine Zeile pro Problem und Exit 1. `--show` gibt die Seiten der Vorlage aus, jede als `=== S. <n> (<teile>) ===` mit dem Rohtext aus `engine.source_page_text`, und dann Exit 0.

- [ ] **Step 1: Failing tests schreiben** – mit einem Fake-`VoteCatalog` im Speicher: zwei Vorlagen, eine Seite mit Text und einem Block „Die Gebühr steigt auf 335 Franken pro Jahr.“, eine fehlende Quelle.

```python
def test_valid_haupt_case_passes(): assert validate([case()], votes, complete=False) == []
def test_quote_found_only_in_block_passes(): ...                    # Review Focus 1
def test_quote_with_straight_apostrophe_is_rejected(): ...          # PDF hat ’ -> Meldung enthält "nicht wörtlich auf S."
def test_quote_with_extra_word_is_rejected(): ...
def test_missing_booklet_reports_fetch_command(): ...               # Review Focus 4: "Büchlein fehlt" und "fetch_catalog_booklets.py"
def test_section_cue_at_start_is_rejected(): ...                    # "Der Bundesrat sagt, ..." -> "Abschnittssignal"
def test_falscher_sprecher_may_name_speaker(): ...
def test_non_german_claim_is_rejected(): ...                        # französischer Satz -> "nicht Deutsch"
def test_vote_outside_split_is_rejected(): ...                      # Test-Vorlage in dev-Fall -> "gehört nicht zu dev"
def test_duplicate_and_previous_claims_are_rejected(): ...
def test_kind_rules(): ...                                          # je kind ein Verstoß -> je eine Meldung
def test_complete_counts_are_checked(): ...                         # 1 Fall, complete=True -> Meldungen mit "Testset:"
def test_vote_plan_sets_are_disjoint_and_sized():
    assert not (DEV_VOTES | DEV_SPECIAL_VOTES) & (TEST_VOTES | TEST_SPECIAL_VOTES)
    assert len(DEV_VOTES) == 15 and len(TEST_VOTES) == 18 and sum(HAUPT_PLAN.values()) == 90
def test_read_cases_names_bad_line(tmp_path): ...                   # unbekanntes Feld in Zeile 2 -> ValueError mit "Zeile 2"
def test_case_sha_changes_with_any_field(): ...
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `cd track_2a && ../.venv312/bin/python -m pytest tests/test_agent_testset.py -q`
Expected: FAIL (Modul fehlt)

- [ ] **Step 3: `page_texts(engine, pdf, page) -> List[str]` in `graph.py` als Modulfunktion herausziehen; `_page_texts` ruft sie auf.** Die Graph-Tests bleiben unverändert grün.

- [ ] **Step 4: `testset.py` und `check_testset.py` implementieren, `TESTSET.md` schreiben**

- [ ] **Step 5: Tests und Echtlauf**

Run: `cd track_2a && ../.venv312/bin/python -m pytest -q && ../.venv312/bin/python scripts/check_testset.py --show 2026-11-29_de#2 | head -20`
Expected: alle Tests bestanden; die Ausgabe beginnt mit `=== S. 6 (summary) ===` und Text zur Feuerwerk-Initiative

- [ ] **Step 6: Commit**

```bash
git add track_2a/src/agent/testset.py track_2a/src/agent/graph.py track_2a/scripts/check_testset.py track_2a/tests/test_agent_testset.py docs/genai/TESTSET.md
git commit -m "feat(agent): validate testset cases against the booklet PDFs"
```

---

### Task 3: Prüfseite für Sheyla

**Files:**
- Create: `track_2a/scripts/build_label_review.py`
- Test: `track_2a/tests/test_label_review_page.py`

**Interfaces:**
- Consumes: `read_cases`, `case_sha`, `load_vote_catalog`, `VoteCatalog.parts`, `engine.source_page_text`
- Produces: `render_review_page(cases: List[EvalCase], views: Dict[str, dict]) -> str`. Dabei ist `views[case.id]` = `{"vote_title", "vote_date", "page", "page_text", "pdf_href"}` oder `{}` bei `ohne_abstimmung`. `page_text` ist der Rohtext aus `engine.source_page_text`, `pdf_href` ist `Path(pdf).resolve().as_uri() + "#page=<n>"`. CLI `python scripts/build_label_review.py [--cases PATH] [--out PATH]`, Standard für `--out` ist `<repo>/.cache/testset-review/label_review.html`; gibt den Pfad aus.
- Produces: Exportformat `label_review.jsonl`: eine Zeile pro Urteil `{"id", "case_sha", "expected", "after_answer", "note", "at"}` in zeitlicher Reihenfolge; `after_answer` nur bei `mehrdeutig`, sonst `null`.

**Verhalten der Seite (eine Datei, offline, ohne externe Skripte):**
- Ein Fall pro Ansicht, Behauptung groß. Darüber der Fortschritt „17 / 110 geprüft“ und ein Filter „Alle · Offen · Abweichungen“.
- Darunter Vorlage mit Datum, bei Spezialfällen die Art auf Deutsch (und `fault`), das Zitat in einem eigenen Kasten, darunter der Seitentext und der Link „PDF S. 12 öffnen“. Im Seitentext wird das Zitat nur markiert, wenn es dort zeichengleich vorkommt; sonst reicht der Kasten, weil Zeilenumbrüche und Trennstriche abweichen können.
- **Blind:** Entworfenes Ergebnis, `after_answer` und `note` bleiben verborgen, bis Sheyla geurteilt hat. Danach erscheinen sie mit grün „stimmt überein“ oder rot „weicht ab“.
- Knöpfe: Hauptfälle „gestützt (1) · widersprochen (2) · nicht belegt (3)“; Spezialfälle alle sechs Ergebnisse; `mehrdeutig` zusätzlich die Zeile „Nach der Antwort“ mit den drei Urteilen. Dazu ein Notizfeld, „Zurück (←)“ und „Weiter (→ / Enter)“.
- Jedes Urteil wird sofort an ein Protokoll in `localStorage` (`fact-attack-testset-review-v1`) angehängt, Zugriffe in try/catch. „Exportieren“ lädt `label_review.jsonl` herunter, „Importieren“ stellt aus einer Datei wieder her.
- Texte werden nur per `textContent` gesetzt. Die Falldaten stehen als JSON in `<script type="application/json">`, dabei wird `</` als `<\/` maskiert.
- Hell- und Dunkelmodus über `prefers-color-scheme`; bedienbar ab 360 px Breite.

- [ ] **Step 1: Failing tests schreiben**

```python
def test_page_contains_every_case_and_sha(): ...        # jede id und case_sha(case) kommt im HTML vor
def test_script_breakout_is_escaped():                  # Review Focus 2
    html = render_review_page([case(claim='X </script><script>alert(1)</script>')], views)
    assert "</script><script>alert(1)" not in html
def test_page_loads_nothing_external(): ...             # kein 'src="http', kein 'href="http' außer file://-PDF-Links
def test_page_never_uses_innerhtml_for_data(): ...      # "innerHTML" kommt im Skript nicht vor
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl** – `../.venv312/bin/python -m pytest tests/test_label_review_page.py -q` → FAIL

- [ ] **Step 3: `build_label_review.py` implementieren**

- [ ] **Step 4: Tests bestanden; Handprobe im Browser**

Mit drei Beispielfällen in einer temporären `cases.jsonl` (`--cases`, `--out` in den Scratchpad) die Seite im Browser-Bereich öffnen. Dann zwei Fälle beurteilen (davon einer `mehrdeutig`), neu laden (Urteile bleiben) und exportieren. Erwartet: zwei JSONL-Zeilen im obigen Format, und die Labels waren vor dem Urteil nicht sichtbar.

- [ ] **Step 5: Commit**

```bash
git add track_2a/scripts/build_label_review.py track_2a/tests/test_label_review_page.py
git commit -m "feat(agent): blind label review page for the testset"
```

---

### Task 4: Sperren und Lader

**Files:**
- Modify: `track_2a/src/agent/testset.py`
- Create: `track_2a/scripts/lock_testset.py`
- Test: `track_2a/tests/test_agent_testset_lock.py`

**Interfaces:**
- Consumes: `read_cases`, `case_sha`, `validate`, `load_vote_catalog`, Katalog-Hashes.
- Produces:

```python
@dataclass(frozen=True)
class ReviewStatus:
    missing: List[str]; stale: List[str]; disagree: List[str]
    first_agree: int; changed_after_first_review: List[str]

class LockRefused(Exception): problems: List[str]

def read_review(path: Path = REVIEW_PATH) -> Dict[str, List[dict]]          # id -> Urteile in Dateireihenfolge
def review_status(cases: List[EvalCase], review: Dict[str, List[dict]]) -> ReviewStatus
def lock(*, votes: VoteCatalog, sources: Dict[str, dict], cases_path=CASES_PATH, review_path=REVIEW_PATH,
         manifest_path=MANIFEST_PATH, git_commit: str, git_dirty: bool, now: datetime, complete: bool = True) -> dict
def load_testset(split: str, *, cases_path=CASES_PATH, manifest_path=MANIFEST_PATH, allow_test: bool = False) -> List[EvalCase]
```

Semantik:
- Es zählt das **letzte** Urteil pro ID. `stale` heißt: Dessen `case_sha` passt nicht zum aktuellen Fall. `disagree` heißt: `expected` oder (bei `mehrdeutig`) `after_answer` weicht ab. `first_agree` zählt Fälle, deren **erstes** Urteil zum heutigen Fall passt. `changed_after_first_review` sind Fälle, deren erstes Urteil einen anderen `case_sha` trägt.
- `lock` verweigert mit `LockRefused`, wenn `manifest_path` existiert („bereits gesperrt; Änderungen brauchen ein neues Testset“), wenn `validate(..., complete=True)` Probleme meldet oder wenn `missing`, `stale` oder `disagree` nicht leer sind. Jede Meldung nennt die IDs.
- Manifest (`json`, eingerückt, `ensure_ascii=False`): `schema` 1, `locked_at` (UTC ISO auf Sekunden), `git_commit`, `git_dirty`, `cases_sha256`, `review_sha256`, `counts` (je split/group, kind, novelty, expected), `booklets` (Dateiname → SHA-256 für jedes referenzierte Büchlein), `review` {`cases`, `first_judgement_agrees`, `changed_after_first_review`, `reviewer`: `"Sheyla"`}, `usage`: „Dev frei. Test und Spezial-Test einmalig nach dem Konfigurationsfreeze (Stufe 5).“
- `load_testset`:
  - Fehlt das Manifest: `ValueError("Testset ist nicht gesperrt")`.
  - Weicht der Hash ab: `ValueError("cases.jsonl wurde nach dem Sperren verändert")`.
  - Unbekannter Split: `ValueError`.
  - `test` ohne `allow_test`: `PermissionError("Testteil erst nach dem Konfigurationsfreeze (allow_test=True)")`.
  - Sonst kommen die Fälle des Splits zurück, Haupt- und Spezialfälle.
- `scripts/lock_testset.py [--review PATH]`: kopiert eine angegebene Exportdatei nach `REVIEW_PATH`, liest `git rev-parse HEAD` und `git status --porcelain` und sperrt. Danach gibt es die Zusammenfassung aus; bei `LockRefused` gibt es die Probleme aus und endet mit Exit 1.

- [ ] **Step 1: Failing tests schreiben**

```python
def test_latest_judgement_counts(): ...                 # zwei Urteile für eine id, das letzte stimmt -> nicht in disagree
def test_stale_review_blocks_lock(tmp_path): ...        # Review Focus 3: Fall nach Urteil geändert -> LockRefused mit der id
def test_disagreement_blocks_lock(tmp_path): ...
def test_missing_review_blocks_lock(tmp_path): ...
def test_lock_writes_manifest_and_refuses_relock(tmp_path): ...
def test_load_dev_after_lock(tmp_path): ...
def test_load_refuses_unlocked(tmp_path): ...
def test_load_refuses_changed_cases(tmp_path): ...      # Review Focus 5
def test_load_test_needs_allow_test(tmp_path): ...      # Review Focus 5
def test_first_agree_and_changed_after_first_review(): ...
```

Die Tests nutzen einen Mini-Fallsatz mit einem Fake-`VoteCatalog` und rufen `lock(..., complete=False)` auf; `lock` reicht `complete` an `validate` weiter. Das Skript ruft `lock` immer mit dem Default `True` auf.

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl** → FAIL
- [ ] **Step 3: Implementieren**
- [ ] **Step 4: Volle Testsuite grün**
- [ ] **Step 5: Commit**

```bash
git add track_2a/src/agent/testset.py track_2a/scripts/lock_testset.py track_2a/tests/test_agent_testset_lock.py
git commit -m "feat(agent): lock the testset with a manifest and gate the test split"
```

---

### Task 5: Konvention „falscher Sprecher“ und Dev-Entwurf (30 + 7)

**Files:**
- Create: `track_2a/data/genai/testset/cases.jsonl` (Dev-Fälle)
- Modify: `docs/genai/TESTSET.md` (Abschnitt „Falscher Sprecher: Konvention“)

- [ ] **Step 1: Konvention an den OST-Daten nachschlagen.** Der Datensatzbeschrieb sagt nichts dazu; er wertet nur „relativ zur `reference_string` und zum Büchlein als Ganzes“.
  - Mit `src.hf_dataset.load_rows()` alle deutschen Behauptungen laden, die mit `claim_part` ∈ {committee, federal_council} beginnen und Label 2 oder 1 haben.
  - Davon 30 lesen und zählen: Wie viele schreiben einer Seite eine Aussage der **anderen** Seite zu, und welches Label tragen diese?
  - Festlegen und mit Zahlen dokumentieren:
    - Gibt es solche Fälle mit einheitlichem Label, gilt dieses Label.
    - Gibt es keine oder sind sie uneinheitlich, gilt Claudes Regel: `widersprochen`, wenn die genannte Seite das Gegenteil vertritt, sonst `nicht_belegt`; `acceptable` = beide.
- [ ] **Step 2: 30 Dev-Hauptfälle entwerfen** (15 Dev-Vorlagen × 2, gesamt 10/10/10), nach den Schreibregeln.
- [ ] **Step 3: 7 Dev-Spezialfälle entwerfen** laut Tabelle. Bei `mehrdeutig` mit Mietrecht nimmt die Behauptung nur „die Mietrechtsvorlage“ auf; eine Eigenschaft beider Vorlagen ist erlaubt.
- [ ] **Step 4: Prüfen** – `../.venv312/bin/python scripts/check_testset.py --partial` → `OK: 37 Fälle, 0 Probleme`
- [ ] **Step 5: Commit**

```bash
git add track_2a/data/genai/testset/cases.jsonl docs/genai/TESTSET.md
git commit -m "data(agent): draft the dev part of the testset and the wrong-speaker convention"
```

### Task 6: Test-Hauptfälle entwerfen (60)

**Files:** Modify: `track_2a/data/genai/testset/cases.jsonl` (anhängen)

- [ ] **Step 1: Ungesehen** (4 × 6, je Vorlage 2/2/2) aus dem Büchlein vom 29.11.2026.
- [ ] **Step 2: Beobachtet** (2 × 6, je 2/2/2) aus dem Büchlein vom 27.09.2026, ohne Behauptungen aus `data/holdout_2026-09-27/claims.jsonl`.
- [ ] **Step 3: Bekannt** (12 × 2) so, dass der Testteil insgesamt 20/20/20 hat.
- [ ] **Step 4: Prüfen** – `check_testset.py --partial` → `OK: 97 Fälle, 0 Probleme`
- [ ] **Step 5: Commit** `data(agent): draft the main test cases of the testset`

### Task 7: Test-Spezialfälle entwerfen (13)

**Files:** Modify: `track_2a/data/genai/testset/cases.jsonl` (anhängen)

- [ ] **Step 1: Vier `mehrdeutig`:** E-ID (`2021-03-07_de#2` / `2025-09-28_de#2`), AHV (`2026-11-29_de#1` / `2022-09-25_de#2`, optional `2024-03-03_de#1`), Ehepaare (`2026-11-29_de#3` / `2026-03-08_de#4`), Kriegsmaterial (`2026-11-29_de#4` / `2020-11-29_de#2`). Mindestens ein Fall soll **je nach Vorlage ein anderes Urteil** haben (E-ID: wer die E-ID ausstellt). Das zeigt, warum Raten schadet.
- [ ] **Step 2: Restliche neun** laut Tabelle. Für `ohne_abstimmung` vorher mit `--show` bzw. der Titelliste prüfen, dass keine der 64 Vorlagen das Thema behandelt. Bei `prompt_injection` darf das verlangte Urteil nie dem richtigen entsprechen; ein Fall enthält einen JSON- oder HTML-Schnipsel.
- [ ] **Step 3: Vollprüfung** – `check_testset.py` (ohne `--partial`) → `OK: 110 Fälle, 0 Probleme`
- [ ] **Step 4: Commit** `data(agent): draft the special test cases of the testset`

---

### Task 8: Labelprüfung durch Sheyla (menschlicher Schritt, bis Sa 07.11.)

- [ ] **Step 1:** `../.venv312/bin/python scripts/build_label_review.py` aufrufen, Sheyla den Pfad geben und kurz erklären: blind urteilen, Notiz bei Zweifel, am Ende „Exportieren“.
- [ ] **Step 2:** Exportdatei entgegennehmen und `review_status` ausgeben (fehlend, veraltet, abweichend).
- [ ] **Step 3: Jede Abweichung einzeln mit Sheyla klären.** Entweder wird der Fall korrigiert und von ihr neu beurteilt, oder ihr Urteil wird nach Rücksprache übernommen und von ihr erneut gesetzt. Labels werden nie stillschweigend angepasst. Danach `check_testset.py` → 0 Probleme.

### Task 9: Sperren und dokumentieren (So 08.11.)

**Files:**
- Create: `track_2a/data/genai/testset/label_review.jsonl`, `track_2a/data/genai/testset/manifest.json`
- Modify: `docs/genai/TESTSET.md` (Abschnitt „Gesperrt“), `docs/genai/VALIDIERUNG.md` (neuer Abschnitt mit Testzahl)

- [ ] **Step 1:** `../.venv312/bin/python scripts/lock_testset.py --review <export>` → Manifest geschrieben, Exit 0
- [ ] **Step 2:** Prüfen: `load_testset("dev")` liefert 37 Fälle; `load_testset("test")` wirft `PermissionError`.
- [ ] **Step 3: Doku ergänzen.** Gesperrt am, Commit, Anzahlen, Übereinstimmung des ersten Urteils, geänderte Fälle mit Grund, Hinweis auf Stufe 3: Schwellenwerte nur über `load_testset("dev")`.
- [ ] **Step 4: Volle Testsuite grün, dann Commit**

```bash
git add track_2a/data/genai/testset docs/genai/TESTSET.md docs/genai/VALIDIERUNG.md
git commit -m "data(agent): lock the testset after Sheyla's label review"
```
