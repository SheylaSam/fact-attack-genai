# Testset der Agentenschicht (Stufe 2)

01.11.2026, Branch `genai/agent-testset`. Plan:
[agentenschicht-stufe2-testset.md](../superpowers/plans/2026-11-01-agentenschicht-stufe2-testset.md),
Design: [agentenschicht-design.md](../superpowers/specs/2026-10-31-agentenschicht-design.md).

## Zweck

Das Testset misst später (Stufe 5) den Agenten, und es legt vorher (Stufe 3) die Schwellenwerte
fest. Es besteht aus 90 frei formulierten deutschen Behauptungen (30 Dev, 60 Test) und 20
Spezialfällen (7 Dev, 13 Test). Jeder Fall hat ein erwartetes Ergebnis und eine **wörtliche**
Fundstelle im Büchlein. Sheyla prüft jedes Label blind, danach wird das Testset gesperrt, **bevor**
Schwellenwerte kalibriert werden.

Die Fälle entstehen nur aus dem Büchleintext (`scripts/check_testset.py --show <vote_id>`), ohne
Agent, ohne Apertus und ohne Web. Kein Fall geht vor dem Sperren durch den Agenten, den Prüfpfad oder
ein anderes Modell. Das Modul `track_2a/src/agent/testset.py` und das Skript
`track_2a/scripts/check_testset.py` prüfen jeden Fall deterministisch gegen die PDFs.

## Vorlagenplan

Aufgeteilt wird nach Vorlage (`vote_id` aus `SourceIndex`, Form `<datum>_de#<k>`). Jede Vorlage
gehört genau einer Seite an. Paraphrasen derselben Aussage bleiben dadurch automatisch im selben Split.
Die Mengen stehen im Code als `DEV_VOTES`, `TEST_VOTES`, `DEV_SPECIAL_VOTES`, `TEST_SPECIAL_VOTES` und
`HAUPT_PLAN` (Vorlage → Anzahl Hauptfälle); ein Test hält sie gleich diesem Abschnitt.

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
| `falscher_sprecher` | 1 | 2 | `widersprochen` (Konvention „Falscher Sprecher“, unten) | Argument der einen Seite der anderen zugeschrieben |
| `zusammengesetzt` | 1 | 2 | `rueckfrage`; entscheidet ein Teil schon das Urteil des ganzen Satzes, steht dieses Urteil zusätzlich in `acceptable`: ein klar falscher Teil macht den ganzen Satz `widersprochen`, ein wahrer Teil plus ein Teil, den das Büchlein offenlässt, macht ihn `nicht_belegt` | zwei unabhängige Aussagen in einem Satz |
| `ohne_abstimmung` | 1 | 2 | `quelle_fehlt` | Dev: Cannabis (statt „Euro statt Franken“: die Bargeld-Vorlage 2026-03-08 #1 regelt den Franken als Währung). Test: Themen ohne Vorlage in den 64 Vorlagen |
| `api_ausfall` | 1 | 1 | `technischer_fehler` | normale Behauptung, `fault` `timeout` bzw. `http_500` |
| `prompt_injection` | 1 | 2 | Urteil der echten Aussage | eingebettete Anweisung, einmal als JSON/HTML-Schnipsel |

Neuheit nach Datum: `2026-11-29` → `ungesehen`, `2026-09-27` → `beobachtet` (frühere Experimente
haben dieses Büchlein gesehen, die neuen Behauptungen sind aber neu), sonst `bekannt`. Die Zuordnung
macht `testset.novelty_for(vote_id)`.

## Schema von `cases.jsonl`

Datei: `track_2a/data/genai/testset/cases.jsonl`, eine Zeile pro Fall, UTF-8. Jede Zeile hat alle
Felder von `EvalCase` und zusätzlich `"claim_language": "de"`. Fehlt ein Feld oder ist eines
unbekannt, nennt `read_cases` die Zeile (`ValueError`). `claim_language` ist kein Teil des Falls und
geht nicht in den Hash ein; der Leser verlangt `"de"` auf jeder Zeile.

| Feld | Typ | Bedeutung |
|---|---|---|
| `id` | Text | `dev-001` … `dev-030` und `test-001` … `test-060` (Hauptfälle), `sdev-01` … `sdev-07` und `stest-01` … `stest-13` (Spezialfälle) |
| `split` | `dev` oder `test` | |
| `group` | `haupt` oder `spezial` | |
| `kind` | Art oder `null` | `null` genau bei `haupt`; bei `spezial` eine der sechs Arten |
| `novelty` | `bekannt`, `beobachtet`, `ungesehen` oder `null` | nach Datum der Vorlage; `null` bei `ohne_abstimmung` |
| `claim` | Text | die Behauptung, deutsch, höchstens 1000 Zeichen |
| `expected` | Ergebnis | eines der sechs Ergebnisse von `graph.Outcome`: `gestuetzt`, `widersprochen`, `nicht_belegt`, `rueckfrage`, `quelle_fehlt`, `technischer_fehler` |
| `acceptable` | Liste | Hauptfälle: leer. Spezialfälle: Ergebnisse, die nicht falsch sind; enthält `expected` |
| `vote_id` | Text oder `null` | die Vorlage der Fundstelle; bei `mehrdeutig` die Vorlage, die nach der Antwort auf die Rückfrage gemeint ist; `null` genau bei `ohne_abstimmung` |
| `ambiguous_with` | Liste | nur `mehrdeutig`: die anderen Vorlagen, zu denen die Behauptung ebenso passt (nicht `vote_id`) |
| `after_answer` | Urteil oder `null` | nur `mehrdeutig`: das Urteil, wenn die Rückfrage mit `vote_id` beantwortet ist |
| `page` | Zahl oder `null` | 1-basierte PDF-Seite der Fundstelle |
| `quote` | Text oder `null` | wörtliches Zitat von dieser Seite |
| `fault` | `timeout`, `http_500` oder `null` | nur `api_ausfall`: der eingespielte Fehler |
| `note` | Text | Begründung; bei Spezialfällen und bei `nicht_belegt` Pflicht |

`case_sha(case)` ist der SHA-256 von `json.dumps(asdict(case), sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`. Ein geprüftes Urteil ist an diesen Hash gebunden: Ändert sich ein Feld, ist
das Urteil veraltet.

Beispielzeile (Hauptfall, gekürzt; ID, Vorlage und Zitat sind erfunden und gehören zu keinem echten Fall):

```json
{"id": "dev-000", "split": "dev", "group": "haupt", "kind": null, "novelty": "bekannt", "claim": "…", "expected": "gestuetzt", "acceptable": [], "vote_id": "2000-01-01_de#1", "ambiguous_with": [], "after_answer": null, "page": 1, "quote": "Beispielzitat, nur zur Veranschaulichung des Formats.", "fault": null, "note": "", "claim_language": "de"}
```

## Schreibregeln für Entwürfe

1. Quelle ist nur der Text aus `check_testset.py --show <vote_id>`; kein Agent, kein Apertus, kein Web.
2. Formuliere wie eine Stimmbürgerin, 8–40 Wörter, ein einziger Satz mit einer Aussage. Am Anfang steht kein Abschnittssignal wie „Der Bundesrat …“, „Das Komitee …“, „Laut Abstimmungstext …“ oder „Bei einem Ja …“; der Validator prüft das mit `part_context.claim_part`. Etwa die Hälfte nennt die Vorlage alltagssprachlich („die Feuerwerk-Initiative“), die anderen beschreiben nur den Inhalt.
3. Hauptfälle betreffen **Tatsachen, die das Büchlein als Tatsache darstellt**: was sich ändert, Zahlen, Fristen, Zuständigkeiten, heutiger Zustand. Umstrittene Argumente einer Seite gibt es nur als Spezialfall `falscher_sprecher`.
4. **gestützt:** Das Büchlein sagt es, umformuliert und nicht abgeschrieben. **widersprochen:** Das Büchlein sagt klar etwas anderes, etwa eine andere Zahl, Richtung, Zuständigkeit oder ein anderes Datum. **nicht belegt:** plausibel und zum Thema der Vorlage, aber das Büchlein entscheidet es weder dafür noch dagegen. `note` erklärt, warum.
5. `page` und `quote`: die eine Seite, die das Label begründet; bei „nicht belegt“ die nächstliegende Stelle. Das Zitat wird zeichengenau aus der `--show`-Ausgabe kopiert, 20–400 Zeichen. Höchstens zwei Fälle pro Vorlage teilen sich ein Zitat.
6. Keine Behauptung aus `data/holdout_2026-09-27/claims.jsonl` oder `data/genai/ost_june_dev.jsonl` übernehmen; der Validator prüft exakte Duplikate.
7. Am Ende steht `check_testset.py --partial` ohne Probleme.

Regel 5, zweiter Satz („höchstens zwei Fälle pro Vorlage teilen sich ein Zitat“), prüft der Validator
nicht; er gilt für das Entwerfen und für die Durchsicht.

## Falscher Sprecher: Konvention

Der Spezialfall `falscher_sprecher` schreibt ein Argument der einen Seite (Komitee oder Bundesrat) der
anderen Seite zu. Der Datensatzbeschrieb des OST-Datensatzes regelt das nicht; er wertet nur „relativ
zur `reference_string` und zum Büchlein als Ganzes“. Die Konvention stammt darum aus den OST-Daten
selbst (nachgeschlagen am 01.11.2026, `src.hf_dataset.load_rows()`, 1488 Zeilen).

**Grundmenge.** 495 deutsche Behauptungen. 264 davon nennen am Anfang eine Seite (`claim_part`:
`committee` 102, `federal_council` 162). Label 2 oder 1 tragen 169: `committee` 28 × Label 2 und
48 × Label 1, `federal_council` 47 × Label 2 und 46 × Label 1.

**Stichprobe.** 30 der 169, gezogen mit `random.Random(20261101).sample(…, 30)` in der Reihenfolge von
`load_rows()`. Gelesen wurde jede Behauptung mit ihrer `reference_string`; bei Bedarf auch die Argumente
der Gegenseite im Büchlein oder in anderen Zeilen derselben Vorlage.

| Gruppe | Anzahl | Label |
|---|---:|---|
| Die genannte Seite bekommt eine Aussage der Gegenseite | 6 | alle 2 |
| Nur die Aussage der genannten Seite ist umgekehrt, die Gegenseite sagt es so nicht | 3 | alle 2 |
| Thema einer anderen Vorlage oder frei erfunden | 21 | alle 1 |

Die sechs Fälle mit falschem Sprecher:

- hf-1047 (Justiz-Initiative): Der Bundesrat soll das Losverfahren loben. Das ist das Argument des
  Komitees.
- hf-1113 (Justiz-Initiative): Das Komitee soll das heutige Wahlsystem loben. Das ist das Argument des
  Bundesrats.
- hf-1088 (CO2-Gesetz): Das Komitee soll das Gesetz kostengünstig, klimawirksam und sozial gerecht
  nennen. So argumentiert der Bundesrat.
- hf-1126 (Nationalstrassen): Der Bundesrat soll zum Nein raten, weil die heutigen Strassen genügen.
  Das ist die Linie des Referendumskomitees.
- hf-1024 (Prämien-Entlastung): Der Bundesrat soll sagen, die Initiative fördere die Kostenkontrolle.
  Das Komitee argumentiert so, es spricht von einem neuen Anreiz für Bund und Kantone.
- hf-1154 (E-ID 2025), ein Grenzfall: Der Bundesrat soll sagen, Firmen und Behörden dürften alle Daten
  speichern. Das ist eine zugespitzte Form der Datenschutzwarnung des Komitees.

In allen sechs vertritt die genannte Seite im Büchlein ausdrücklich das Gegenteil. Ohne hf-1154 bleiben
fünf, das Bild ist dasselbe. Die drei reinen Umkehrungen sind hf-1000 (Kostenbremse), hf-1001
(SRG-Initiative) und hf-1046 (Indonesien).

Ergänzend wurden alle 94 Behauptungen mit Label 1 aus der Grundmenge durchgesehen, nicht nur die 21 aus
der Stichprobe. Keine schreibt einer Seite ein Argument der Gegenseite zu. Label 1 steht dort für ein
fremdes Thema (z. B. hf-0510: Geldspielgesetz bei der Pflegeinitiative) oder für erfundenen Inhalt
(z. B. hf-0644: Verhüllungsverbot steigere die Lebenserwartung).

**Regel.** Die Fälle tragen einheitlich Label 2, also gilt dieses Label:

- `falscher_sprecher` hat `expected` = `widersprochen` und `acceptable` = [`widersprochen`].
- `page` und `quote` zeigen die Stelle, an der die genannte Seite das Gegenteil sagt.
- Die Regel ist nur für den Fall belegt, dass die genannte Seite im Büchlein das Gegenteil vertritt.
  Einen falschen Sprecher, bei dem die genannte Seite zum Punkt schweigt, gibt es in den OST-Daten
  nicht. Solche Fälle werden nicht geschrieben: Wer entwirft, wählt ein Argument, dem die genannte
  Seite ausdrücklich widerspricht.
- Die Ersatzregel für fehlende oder uneinheitliche Daten (`widersprochen` oder `nicht_belegt`, beide
  in `acceptable`) wird damit nicht gebraucht.

## Prüfregeln

`validate(cases, votes, previous_claims=…, complete=True)` liefert eine Meldung pro Problem. Jede
beginnt mit der Fall-ID, die Zählregeln des fertigen Testsets mit `Testset:`.

- **Felder:** Das ID-Muster passt zu `split` und `group` (und liegt im Bereich 1 bis zur Anzahl). IDs sind
  eindeutig. `split` ∈ {dev, test}, `group` ∈ {haupt, spezial}. `kind` ist `None` genau bei `haupt`.
- **Behauptung:** nicht leer, höchstens 1000 Zeichen (`settings.MAX_CLAIM_CHARS`), `guess_language` ergibt
  `de`, nach `official_normalize` eindeutig im Set und nicht unter den früheren Behauptungen (Holdout und
  OST-Dev). Am Anfang steht kein Abschnittssignal (`claim_part` ist `None`), ausser bei `falscher_sprecher`;
  dort darf die Behauptung eine Seite nennen.
- **Vorlage:** `vote_id` ist `None` genau bei `ohne_abstimmung`; sonst ist sie bekannt und ihr Büchlein
  liegt lokal vor. Fehlt das PDF (frischer Klon), lautet die Meldung
  `<id>: Büchlein fehlt (<datei>): scripts/fetch_catalog_booklets.py ausführen`; es gibt keinen Absturz und
  keine Folgemeldung zu Seite und Zitat. Die Vorlage gehört zur Menge ihres Splits und ihrer Gruppe: Hauptfälle
  nur zu `DEV_VOTES` bzw. `TEST_VOTES`, Spezialfälle auch zu den reservierten Vorlagen des Splits. `novelty`
  entspricht dem Datum der Vorlage (bei Spezialfällen der Vorlage des Falls); `ohne_abstimmung` hat `None`.
- **Fundstelle** (bei gesetzter `vote_id`): `page` liegt in `VoteRef.pages`. `official_normalize(quote)` hat
  20 bis 400 Zeichen und steht im normalisierten Text der Seite **oder** eines ihrer Textblöcke
  (`graph.page_texts`). Das ist genau die Regel, nach der die Belegkontrolle des Agenten ein Zitat annimmt:
  ’ statt ', « » statt `"` oder ein ergänztes Wort gelten als nicht wörtlich, Gross-/Kleinschreibung, Leerraum
  und Trennstriche am Zeilenende nicht. Eine Seite ohne lesbaren Text hat eine eigene Meldung.
- **Ergebnisse:** Bei `haupt` ist `expected` eines der drei Urteile. Pro Art:

  | `kind` | Regel |
  |---|---|
  | `mehrdeutig` | `expected` = `rueckfrage`; `after_answer` ∈ Urteile; `ambiguous_with` nicht leer, bekannt, ohne `vote_id`, gleiche Split-Menge |
  | `zusammengesetzt` | `expected` = `rueckfrage` |
  | `ohne_abstimmung` | `expected` = `quelle_fehlt`; `page`, `quote` und `novelty` sind `None` |
  | `api_ausfall` | `expected` = `technischer_fehler`; `fault` ∈ {timeout, http_500} |
  | `falscher_sprecher` | `expected` ∈ {widersprochen, nicht_belegt} |
  | `prompt_injection` | `expected` ∈ Urteile |

  `fault`, `after_answer` und `ambiguous_with` sind nur bei ihrer Art gesetzt. `acceptable` ist leer bei
  `haupt`; bei `spezial` enthält es `expected` und nur Ergebnisse aus `OUTCOMES`. `note` ist nicht leer bei
  `spezial` und wenn `expected` oder `after_answer` = `nicht_belegt`.
- **Zählung (nur bei `complete=True`, also ohne `--partial`):** je Split und Gruppe 30/60 Haupt- und 7/13
  Spezialfälle; je Klasse (gestützt, widersprochen, nicht belegt) 10 (Dev) bzw. 20 (Test) Hauptfälle; je
  Vorlage so viele Hauptfälle wie `HAUPT_PLAN` (2 oder 6), bei Vorlagen mit 6 Fällen je Klasse 2; je Art so
  viele Spezialfälle wie `KIND_COUNTS`.

## Gesperrt

Am 13.11.2026 gesperrt, auf dem Branch `genai/agent-stufe3`. Er enthält `genai/agent-testset` (8d97bee)
unverändert. `track_2a/data/genai/testset/manifest.json` hält fest:
- den Commit 48b39e5 bei sauberem Arbeitsbaum,
- den SHA-256 von `cases.jsonl` (`9d356dd1…`),
- die 22 Büchlein.

110 Fälle: Dev 30 Haupt- und 7 Spezialfälle, Test 60 Haupt- und 13 Spezialfälle.

**Labelprüfung durch Sheyla:**
- Blind auf der Prüfseite, alle 110 Fälle; die Urteile stehen in `label_review.jsonl` (140 Ereignisse).
- Das erste Urteil stimmte bei 82 von 110 Fällen mit dem Entwurf überein.
- Bei den übrigen 28 hat Sheyla nach dem Aufdecken des Entwurfs auf diesen gewechselt, ohne Notiz. Gründe
  waren Lese- und Verständnisfehler, zum Beispiel 108 Ja-Stimmen gegenüber „mehr als 120“ oder ein
  übersehener Hinweis „Simulierter API-Ausfall“.
- Kein Fall wurde nach der Prüfung geändert. Am Ende weicht kein Urteil ab.

**Unabhängige Zweitprüfung durch Codex:**
- codex-cli 0.153.4, Modell `gpt-6-astra`, blind über alle 110 Fälle.
- Codex sah nur, was die Prüfseite vor dem Urteil zeigt: keine Entwürfe, keine Notizen, neutrale Fallnummern.
  Laut Protokoll hat es keine andere Datei gelesen.
- Ergebnis: 110 von 110 gleich wie die Labels, auch bei allen 28 Fällen, die Sheyla nach dem Aufdecken
  geändert hat. Dort stimmte Codex in keinem Fall ihrem ersten Urteil zu.
- Datei: `track_2a/results/genai/testset_label_check_codex.jsonl`, mit Urteil und Begründung pro Fall.

**Grenzen:**
- Die Entwürfe stammen von Claude.
- Die 28 Korrekturen geschahen nach dem Aufdecken. Codex stützt sie, ist aber ein Modell und keine zweite
  Person.
- Felipes Prüfung vom 11.10.2026 betraf das Holdout des Wettbewerbs, nicht dieses Testset.

**Ab jetzt:**
- `load_testset("dev")` liefert 37 Fälle.
- `load_testset("test")` wirft `PermissionError` bis zum Konfigurationsfreeze in Stufe 5
  (`allow_test=True`). Der Freeze war am 14.11.2026 (siehe „Testteil gelesen“).
- Stufe 3 kalibriert die Schwellenwerte des Wächters nur über `load_testset("dev")`.
- Ändert sich `cases.jsonl`, lädt `load_testset` nichts mehr, weil der Hash nicht zum Manifest passt.

## Testteil gelesen (Stufe 5)

Der Testteil ist seit dem 14.11.2026 ausgewertet und damit verbraucht. Ergebnisse und Fehleranalyse:
[EVALUATION_AGENT.md](EVALUATION_AGENT.md).

- **Freeze:** 14.11.2026, 15:30:49 UTC, bei Commit `7bca92b`. Konfig-Hash `c60b30bce0b6…`
  (`track_2a/results/genai/config_freeze.json`). Er schließt den Testset-SHA `9d356dd1…` ein, denselben wie im
  Manifest.
- **Gelesen** nur über den Freeze-Wächter (`scripts/run_agent_eval.py --split test --freeze …`). Jeder Arm lief
  genau einmal und ohne Wiederaufnahme, alle 73 Fälle:

  | Arm | Start (UTC) |
  |---|---|
  | B1 | 15:37:42 |
  | B2 | 15:44:50 |
  | B0 | 15:49:58 |

  Marker: `config_freeze.json.test_run.<arm>`.
- **Auswertung** um 15:52:39 UTC mit `scripts/evaluate_agent_runs.py --freeze …`. Für die Fehleranalyse (Task 7)
  wurden die Gold-Felder des Testteils nach der einmaligen Auswertung programmatisch über den Freeze-Weg
  ausgelesen (`load_testset("test", allow_test=True)`); die Datei wurde nicht verändert, kein Lauf wiederholt.
- **Unverändert:** Nichts unter `data/genai/testset/` wurde geändert. Der Hash passte bei jedem Laden zum Manifest.
- **Vorfall vor dem Freeze (Task 2):** Ein `git grep` eines Implementierers druckte zwei Zeilen aus
  `cases.jsonl` in dessen Kontext, eine davon aus dem Testteil. Verwendet oder geändert wurde nichts, und Sheyla
  wurde informiert. Seitdem schließt jede Suche die Datei aus. Einzelheiten stehen in EVALUATION_AGENT.md,
  „Integrität des Testteils“.
- **Ab jetzt:** Was an diesem Testteil gemessen wurde, gilt nur für die eingefrorene Konfiguration. Eine neue
  Messung nach einer Änderung braucht ein neues Testset.

## Prüfskript

Aus `track_2a/`:

```bash
../.venv312/bin/python scripts/check_testset.py --show 2026-11-29_de#2      # Seiten einer Vorlage, Rohtext
../.venv312/bin/python scripts/check_testset.py --partial                   # Entwurf prüfen, ohne Zählung
../.venv312/bin/python scripts/check_testset.py                             # fertiges Testset prüfen
```

- `--show` druckt jede Seite der Vorlage als `=== S. <n> (<teile>) ===` (die Teile des Büchleins, zu denen
  die Seite gehört, etwa `summary`) und darunter den Rohtext aus `engine.source_page_text`, der Text, aus dem
  das Zitat kopiert wird. Es braucht keine `cases.jsonl`.
- Ohne `--show` liest das Skript `data/genai/testset/cases.jsonl` (oder `--cases PATH`). Es druckt
  `OK: <n> Fälle, 0 Probleme` (Exit 0) oder eine Zeile pro Problem (Exit 1). Fehlt die Datei, steht
  `Keine Fälle: <Pfad> fehlt` (Exit 1).
- Die Engine läuft mit `ApertusClient(mock=True)`: Es verlässt keine Anfrage den Rechner.
- Die PDFs liegen nicht im Repository. Fehlen sie, lädt
  `../.venv312/bin/python scripts/fetch_catalog_booklets.py` sie und prüft den SHA-256 gegen den Katalog.
