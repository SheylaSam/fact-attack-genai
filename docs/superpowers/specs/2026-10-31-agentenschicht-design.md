# Agentenschicht für Fact Attack – Design

Stand: 31.10.2026. Branch `genai/agent-layer`, abgezweigt von `genai/vector-rag` bei `8f591ff`.
Grundlage: [Projektplan vom 25.10.](https://github.com/SheylaSam/fact-attack-genai/blob/main/docs/genai/PROJEKTPLAN.md)
(liegt nur auf `main`), Gespräch mit Sheyla am 31.10., Kursunterlagen SW4–SW7, Messungen in `docs/genai/`.

## Worum es geht

Heute stellt man in der App fünf Dinge selbst ein, bevor eine Prüfung startet: Jahr, Abstimmung,
Sprache des Büchleins, Vorlage und Behauptung. Danach läuft ein fest verdrahteter Prüfpfad
(`ClaimVerificationEngine.verify_claim`). Mit der Agentenschicht tippt man **nur die Behauptung**.
Der Agent sucht mit eigenen Werkzeugen die passende Vorlage und die Belegstellen, fragt nach,
wenn etwas nicht eindeutig ist, lässt Apertus prüfen und kontrolliert die Belege gegen das PDF.
Jeder Schritt ist in der Oberfläche sichtbar.

### Warum jetzt

- Das Modul verlangt einen eigenen LLM-Agenten mit **LangGraph**, pro-code (SW4 Folie 4).
  Laut Semesterprogramm sind folgende Wochenaufträge fällig oder bald fällig. Davon existiert
  im Code noch nichts:
  - SW5: erste lauffähige Version, Tracing funktioniert
  - SW6: MCP-Server anbinden
  - SW7: RAG einbauen
  - SW8 (09.11.): Memory festlegen
  - SW9 (16.11.): Safeguarding (minimal, Prompt-basiert) und Evaluation
  - Präsentation: 23.11.2026, 13:00, 10 Minuten inklusive Fragen
- Coaching (laut Sheyla): Der Scope ist gut. Das Team soll eine Vektor-DB und ein
  Embedding-Modell einbinden, um den Score zu verbessern.
- Sheyla persönlich: Die Oberfläche muss **intuitiv** sein.

### Die Rolle der Vektorsuche (entschieden)

Messung vom 31.10. (`docs/genai/ERGEBNISSE_NLI42.md`): Als *Ersatz* der bestehenden Suche
innerhalb eines bekannten Büchleins verschlechtert die Vektorsuche das Ergebnis. Macro-F1 auf
42 Dev-Fällen: bestehend 1.00, Vektor 0.85, Vektor+BM25 0.72. Gemessen wurde mit dem
JSON-Prompt auf eingefrorenen Kontexten, nicht mit dem Produktionspfad. Diese 42 Fälle nennen
ihren Abschnitt allerdings ausdrücklich. Die bestehende Suche nutzt genau diese Hinweise und ist
bei frei formulierten Behauptungen ungetestet.

SW7 Folie 44 verlangt sinngemäß, aktiv zu entscheiden, ob der Agent RAG braucht, und die
Begründung zu notieren. Entscheidung: **Die Vektorsuche kommt dorthin, wo die bestehende Suche
blind ist.**

1. **Quelle finden:** Eine freie Behauptung wird gegen alle Vorlagen aller Termine abgeglichen.
   Das macht heute der Mensch per Dropdown.
2. **Nachsuche:** Findet die bewährte Suche keinen Beleg, kann der Agent mit Vektoren nachsuchen.

Der bewährte Prüfpfad bleibt unverändert und dient als Vergleichsbasis.

## Ziele

1. Eine frei formulierte deutsche Behauptung führt ohne manuelle Quellenwahl zu einem Urteil mit
   validierten Belegen.
2. Bei Mehrdeutigkeit fragt der Agent nach, statt zu raten. Mehrdeutig sind zum Beispiel:
   - zwei Mietrecht-Vorlagen am 24.11.2024 (Eigenbedarf, Untermiete)
   - zwei E-ID-Gesetze (07.03.2021 und 28.09.2025)
3. Es gibt sechs klar getrennte Ausgänge:
   - **gestützt**, **widersprochen**, **im Dokument nicht belegt**: Das sind die NLI-Klassen.
     „Nicht belegt“ heißt nicht „falsch“.
   - **Rückfrage nötig**, **Quelle fehlt**, **technischer Fehler**: Das sind Zustände des Agenten
     und keine Urteile.
4. Vektor-DB und Embedding-Modell haben eine begründete Rolle, und ihr Beitrag ist gemessen.
5. Die Kursnachweise sind erbracht: LangGraph mit echtem Tool-Use, Tracing, MCP, RAG, Memory,
   Safeguarding, Evaluation.
6. Die Oberfläche ist einfacher als heute, nicht komplizierter.
7. Es gibt eine ehrliche Evaluation B0/B1/B2 auf einem neuen, vor dem Tuning gesperrten Testset.

## Nicht-Ziele

- Allgemeiner Faktencheck im Internet oder Websuche
- Abstimmungsempfehlungen
- Fine-Tuning oder ein neues Modell
- Multi-Agent-System
- Ersatz oder Veränderung des bestehenden Prüfpfads und der Wettbewerbs-CLI. Sie bleiben B0.
- Automatische Quellenfindung für Französisch und Italienisch im MVP. Bei vorgegebener Quelle
  funktionieren alle drei Sprachen, siehe „Oberfläche“. Code und Index dürfen Deutsch nicht fest
  verdrahten.
- Mehrere Behauptungen pro Eingabe über den Agenten. Die bestehende Mehrfachprüfung bleibt.
- Rückbezügliche Folgefragen wie „Und die Untermiete-Vorlage?“ im MVP
- Öffentlicher produktiver Betrieb

## Anti-Ziele: Das wäre ein Misserfolg, auch wenn es technisch läuft

- Der Agent rät eine Vorlage und zeigt ein Urteil zur falschen Vorlage, statt nachzufragen.
- Ein Timeout, API-Fehler oder überschrittenes Budget erscheint als „im Dokument nicht belegt“.
  Der Bestand hat wettbewerbsbedingte Neutral-Fallbacks (`PredictionResult.error`). Die
  Agentenschicht muss sie als technischen Fehler ausweisen.
- Vom Modell formulierter Text erscheint als Beleg. Belege sind immer Originaltext aus dem PDF.
  Mit dem JSON-Prompt zitierte das Modell in 4 von 31 Fällen übersetzt statt wörtlich.
- Die bestehenden Dev-Fälle werden schlechter, weil die Vektorsuche heimlich den bewährten Pfad
  ersetzt.
- Schleifen ohne Ende oder unkontrollierte Apertus-Kosten.
- Das Testset wird angepasst, nachdem man die Ergebnisse gesehen hat.
- Die App wird komplizierter als heute, oder heutige Funktionen fallen weg (Uploads,
  Mehrfachprüfung, FR/IT).

## Rahmenbedingungen

- **LangGraph** ist Pflicht. Versionen aus den Kurs-Notebooks:
  - `langgraph` 1.2.11, `langchain` 1.4.0, `langchain-openai` 1.6.2
  - `mcp` 1.30.0, `langchain-mcp-adapters` 0.3.2

  Sie dienen als Orientierung. Gepinnt wird, was tatsächlich getestet ist.
- **Apertus** über `https://api.inference.cscs.ch/v1`, Modell `swiss-ai/Apertus-v1.5-70B-thinking`
  (`track_2a/src/config.py`). Der Zugang kommt über `FACT_ATTACK_ENV_FILE` aus dem Team-Repo.
  Jeder echte Lauf ist kostenpflichtig und bekommt einen harten Anfrage-Deckel samt Journal, wie
  `scripts/run_nli42.py`.
- **Python 3.12** mit Hash-Lock `track_2a/requirements-genai312-macos-arm64.lock.txt`. Neue Pakete
  kommen in eine eigene `requirements-agent.txt` und ins Lock, `requirements.txt` bleibt
  unverändert.
- Die bestehenden **286 Tests** (Stand `8f591ff`) bleiben grün, insbesondere die Byte-Identität
  der Apertus-Requests in `tests/test_apertus_client.py`.
- Sheyla arbeitet vorerst allein („so weit ich komme“). Deshalb wird in **Stufen** gebaut, und
  jede Stufe ist für sich lauffähig, getestet und vorzeigbar.
- Kursunterlagen kommen nicht ins Repo.
- Die Blockwoche KW45 bedeutet weniger Kapazität.

## Von Sheyla entschieden

| Thema | Entscheidung |
|---|---|
| Umfang | Plan-MVP, zuerst Deutsch |
| Vektorsuche | Quelle finden plus Nachsuche; bewährter Prüfpfad bleibt |
| Autonomie | **Der Agent sucht selbst:** Bei Quellen- und Belegsuche wählt Apertus seine Werkzeuge selbst, in höchstens etwa 5 Schritten. Prüfung, Belegkontrolle und Budget bleiben fest. |
| Oberfläche | Variante B: ein Eingabefeld plus live gefüllte Schrittliste; die Rückfrage erscheint als Knöpfe im betroffenen Schritt |
| Platz in der App | Ersetzt den Ablauf im Tab „Faktencheck“; die manuelle Quellenwahl bleibt aufklappbar als „Quelle selbst wählen“; Verlauf, Mehrfachprüfung und Berichte bleiben |
| Agent-LLM | Apertus |
| Tracing | Kein LangSmith-Zugang: lokales Ablaufprotokoll, LangSmith zuschaltbar per Umgebungsvariable |
| Testset | Claude entwirft Kandidaten mit Fundstelle, Sheyla prüft jedes Label, danach wird gesperrt |
| Kürzungsreihenfolge | Claude überlassen, siehe „Stufen“ |

### Oberfläche, Variante B

```
Was möchtest du prüfen?
[ Die Mietrechtsvorlage stärkt die Vermieter.            ] [Prüfen]

✓ Behauptung verstanden     · Deutsch · Thema Mietrecht
? Quelle finden             Zwei Vorlagen vom 24.11.2024 passen. Welche meinst du?
                            [Kündigung wegen Eigenbedarfs] [Untermiete]
3 Belegstellen suchen
4 Mit Apertus prüfen
5 Belege im PDF kontrollieren
```

Danach folgt die Ergebniskarte mit dem Urteil oder Zustand und den Belegen. Für die Belege gibt
es die Originalseiten-Ansicht mit Markierung schon (`src/pdf_preview.py`). Jeder Schritt zeigt
ein kurzes Detail, zum Beispiel:
- „3 Stellen auf S. 12–14“
- „Nachsuche mit Vektoren“
- welches Werkzeug der Agent gewählt hat

Ein Fehler wird im betroffenen Schritt rot und klar benannt. Die Schrittliste und das
Ablaufprotokoll speisen sich aus denselben Ereignissen. Neue Texte werden über das vorhandene
`src/ui_i18n.py` auf DE, FR und IT übersetzt.

**Alle Wege führen durch den Agenten (Claudes Entscheidung):**
- **„Quelle selbst wählen“:** Startet den Agenten mit fest vorgegebener Quelle. Es gibt
  dieselbe Schrittliste und dieselbe Ergebniskarte, nur entfällt die Quellenfindung. So bleibt
  es ein einziger Ergebnisweg im Tab.
- **Eigene PDF-Uploads:** Sie werden pro Sitzung als Quellen-ID registriert. Die Werkzeuge
  akzeptieren Katalog-IDs oder diese Upload-IDs, nie freie Pfade.
- **Französische oder italienische Behauptung:** Im MVP gibt es dafür keine automatische
  Quellenfindung. Der Schritt „Quelle finden“ bittet dann sichtbar darum, die Quelle selbst zu
  wählen. Mit vorgegebener Quelle funktionieren alle drei Sprachen, weil E5 mehrsprachig ist.
- **Mehrfachprüfung:** Bleibt wie heute und nutzt die Quelle aus „Quelle selbst wählen“. Dort
  ist die Quelle dann Pflicht.

## Von Claude entschieden (bitte beim Lesen prüfen)

### Ablauf und Werkzeuge

Der Graph hat drei Abschnitte:

1. **Suchschleife (agentisch).** Apertus bekommt die Behauptung und wählt selbst aus diesen
   Werkzeugen:
   - `suche_vorlagen`: Vektorsuche über alle Vorlagen des Katalogs; liefert Kandidaten mit Termin,
     Vorlage und Score
   - `suche_passagen`: die bewährte Suche (BM25 und Abschnittslogik) in einer gewählten Vorlage
   - `suche_passagen_vektor`: Vektorsuche in den Seiten einer gewählten Vorlage
   - `rueckfrage`: unterbricht den Lauf und stellt der Person eine Frage mit Antwortknöpfen
   - `quelle_fehlt`: beendet den Lauf ohne Urteil

   Es sind höchstens 5 Werkzeugschritte erlaubt. Die Schleife endet, wenn der Agent eine Vorlage
   und Passagen festlegt.
2. **Prüfung (fest).** NLI über den Produktionspfad im `ids`-Modus, also mit Passagen-IDs, und
   nicht über den JSON-Prompt. Belege sind dadurch Originalseiten bzw. Textblöcke
   (`src/evidence.py`). Dafür bekommt der Bestand höchstens eine kleine Schnittstelle „prüfen
   mit vorgegebenen Passagen“. B0 bleibt byte-identisch.
3. **Belegkontrolle und Ergebnis (fest).**
   - Jedes angezeigte Zitat wird deterministisch gegen den PDF-Text und die Seite geprüft.
   - Ein „gestützt“ oder „widersprochen“ ohne gültigen Beleg wird als „im Dokument nicht belegt“
     angezeigt, mit dem Hinweis „Urteil ohne prüfbaren Beleg verworfen“, und protokolliert.
     Das wird ein seltener Fall sein.

**Nachsuche:** Lautet die erste Prüfung „im Dokument nicht belegt“, geht die Kontrolle genau
einmal an die Suchschleife zurück, mit der Information „kein Beleg gefunden“. Der Agent kann
dann anders suchen, zum Beispiel mit `suche_passagen_vektor`. Danach folgt die zweite und letzte
Prüfung. Das ist die einzige Rückkante zur Suche.

**Tool-Calling:** Zuerst wird natives Tool-Calling mit Apertus in einem kleinen, gedeckelten
Smoke-Test geprüft. Ist es unzuverlässig, gibt Apertus eine JSON-Aktion aus, die gegen die
Werkzeugschemas validiert wird. Diese Einschränkung wird dokumentiert. Die Analyse der
Behauptung (Thema; genannter Termin, Vorlage oder Sprecher) kann Teil des ersten Suchschritts
sein; das entscheidet der Builder.

**Budget:** Pro Prüfung gelten zwei Grenzen:
- höchstens 5 Suchschritte vor der ersten Prüfung, 1 Suchschritt bei der Nachsuche und
  2 Prüfungen
- zusätzlich ein harter Deckel von 12 tatsächlichen Apertus-HTTP-Anfragen, einschließlich
  Übersetzung, Kaskade und Retries im Bestandspfad

Ist eine Grenze erreicht, endet die Prüfung mit „technischer Fehler“.

### Index und Embedding

- **Quellenindex:** Ein Qdrant-Index über die Büchlein des Katalogs, im MVP nur Deutsch. Jede
  Passage trägt als Metadaten Termin, Sprache, Vorlage, Seite und Abschnitt bzw. Sprecher. Die
  Seitenbereiche pro Vorlage liefert der bestehende `part_context.booklet_parts`.
  `suche_vorlagen` bündelt die Passagentreffer pro Vorlage. Die Schwellenwerte für „eindeutig,
  mehrdeutig, keine“, die der Agent als Hinweis bekommt, werden nur auf dem Dev-Teil kalibriert.
- **Katalog:** Er wird um neue Büchlein erweitert (siehe Testset). `load_catalog` akzeptiert
  heute nur `www.bk.admin.ch`, die 27.09.2026-Büchlein liegen aber auf `www.admin.ch`. Die
  erlaubten Hosts werden entsprechend erweitert.
- **Embedding-Modell:** Standard ist das vorhandene `intfloat/multilingual-e5-small`: lokal,
  gepinnte Revision, kein Schlüssel nötig. Das Experiment „Embedding-Modell einbeziehen“
  vergleicht es mit `apertus-v1.1-swiss-embed-0.4b` über den Kurs-Proxy (braucht einen
  `LITELLM_API_KEY`) und optional mit einem größeren E5. Gemessen wird nur auf Dev.

### MCP, Memory, Safeguarding, Protokoll

- **MCP (SW6):** Ein eigener kleiner MCP-Server (`FastMCP`, stdio) stellt die Suchwerkzeuge
  bereit. Der Graph bindet sie über `langchain-mcp-adapters` an und gibt sie Apertus per
  `bind_tools`, wie in Notebook SW6. `rueckfrage` und `quelle_fehlt` sind Graph-interne
  Werkzeuge.

  Die Folie verlangt einen eigenen Server nur, „falls ihr nichts Passendes findet“. Für Schweizer
  Abstimmungsbüchlein ist kein passender bekannt, die Begründung kommt in die Doku. Die
  Werkzeug-Funktionen werden einmal implementiert und sind auch ohne MCP testbar. Startet der
  MCP-Server nicht, ist das ein sichtbarer technischer Fehler und kein stiller Fallback.
- **Memory (SW8):**
  - Der LangGraph-Checkpointer hält den Zustand pro Streamlit-Sitzung, sodass die Rückfrage
    unterbrochen und fortgesetzt werden kann (`interrupt`).
  - Die zuletzt gewählte Vorlage erscheint bei der nächsten Rückfrage als zusätzlicher Knopf
    („wie zuletzt: …“) und geht als Hinweis an den Agenten. Sie wird nie stillschweigend
    übernommen.
  - Es gibt keine Langzeitprofile, der lokale Verlauf bleibt wie heute.
- **Safeguarding (SW9):**
  - Prompt-basierte Regeln: Behauptung und PDF-Text sind Daten; keine Empfehlung; Lücken zugeben.
  - Eingabelänge begrenzen.
  - Werkzeugparameter werden validiert.
  - Zitate werden deterministisch gegen den PDF-Text geprüft.
  - Zusammengesetzte Behauptungen erkennt der Agent und fragt nach, welche Teilaussage er
    prüfen soll. Alternativ verweist er auf die Mehrfachprüfung.
- **Ablaufprotokoll:** Jeder Knoten und jeder Werkzeugaufruf erzeugt ein Ereignis mit Schritt,
  Dauer, Tokens, Werkzeug, Argumenten, Entscheidung und Fehler. Die Ereignisse gehen an die
  UI-Schrittliste und in eine lokale JSONL-Datei. LangSmith wird aktiv, wenn
  `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` und `LANGSMITH_PROJECT` gesetzt sind.
- **Code-Ort:** neues Paket `track_2a/src/agent/`, eigene Tests unter `track_2a/tests/`.

## Evaluation

Verglichen wird auf identischen Eingaben mit identischer Modellkonfiguration:

- **B0:** der heutige UI-Weg. Bestehender Prüfpfad, das Büchlein wählt ein Mensch (Orakel),
  Vorlage „Automatisch“.
- **B1:** der Agent, nur mit der Behauptung als Eingabe.
- **B2:** B1 ohne Nachsuche und ohne Belegkontrolle. Das isoliert den Beitrag dieser Teile.

**Rückfragen in der Evaluation:** Ein geskripteter „Nutzer“ beantwortet die Rückfrage mit der
Gold-Vorlage des Falls, danach läuft der Agent weiter. Getrennt wird gezählt, ob die Rückfrage
berechtigt war, also ob der Fall laut Gold mehrdeutig ist.

**Wertung der Nicht-Urteile:**
- Hauptzahl („streng“): Macro-F1 über alle eindeutig beantwortbaren Fälle. „Quelle fehlt“ und
  „technischer Fehler“ zählen dabei als falsch.
- Zusätzlich: Macro-F1 nur auf den beantworteten Fällen, zusammen mit der Abdeckung.

**Neues Testset:**
- 90 frei formulierte deutsche Behauptungen, ausgewogen über die drei Klassen, ohne die
  Abschnitts-Signalwörter der Hackathon-Claims.
- Aufteilung nach Vorlage: 30 Dev, 60 Test. Paraphrasen derselben Aussage bleiben im selben Split.
- Dazu 20 Spezialfälle: mehrdeutige Vorlage (Mietrecht, E-ID), falscher Sprecher,
  zusammengesetzte Behauptung, Thema ohne Abstimmung, simulierter API-Ausfall, Prompt Injection.
- Das erwartete Ergebnis jedes Spezialfalls wird **vor** dem Sperren festgelegt. Beim falschen
  Sprecher gilt die Labelkonvention des offiziellen OST-Datensatzes; der Builder schlägt sie nach
  und dokumentiert sie.

**Quellen und Neuheit**, in dieser Reihenfolge:
1. Büchlein zum **29.11.2026**, falls bereits veröffentlicht. Sie sind ungesehen.
2. Büchlein zum **27.09.2026** (`data/holdout_2026-09-27/`). Deren alte Holdout-Claims haben
   frühere Experimente beeinflusst, die neuen freien Behauptungen sind aber neu.
3. Bekannte Katalogbüchlein als Auffüllung.

Die Neuheit wird pro Gruppe ausgewiesen. Neue Büchlein kommen in den Katalog und in den Index.
Die Quellenfindung wird dadurch schwieriger, also realistischer.

**Labelprozess:** Claude entwirft jeden Fall mit Seite und wörtlichem Zitat. Sheyla prüft jedes
Label. Danach wird gesperrt: Hash im Manifest, Testteil einmalig nach dem Konfigurationsfreeze
auswerten. Spätere Änderungen erfordern ein neues Testset.

**Metriken:**
- Macro-F1 streng und auf den beantworteten Fällen
- echte Fehlurteile getrennt von Enthaltungen (Abdeckung UND Fehlerquote)
- Quellenfindung: richtige Vorlage, berechtigte und unnötige Rückfragen, falsche Sicherheit
- Anteil gültiger Belege
- Tokens, p50/p95-Latenz, Werkzeugaufrufe, Fehlerquote

Die 20 Spezialfälle werden einzeln ausgewertet.

**Regression:**
- Die 42 bestehenden Dev-Fälle (`data/genai/ost_june_dev.jsonl`, Behauptungen 15 DE, 16 FR,
  11 IT) laufen durch den Agenten **mit vorgegebener Quelle**. Die Quellenfindung entfällt, die
  Suchschleife bleibt auf das gegebene Büchlein beschränkt.
- Referenz ist ein frischer, gepaarter B0-Lauf über den Produktionspfad auf denselben Fällen
  am selben Tag. Die frühere 1.00 stammt aus dem JSON-Prompt und ist dafür nicht die Referenz.
- Kriterium: höchstens ein Fall netto schlechter. Jeder geänderte Fall wird einzeln aufgeführt
  und begründet.

**Kosten:** Jeder echte Lauf hat einen Anfrage-Deckel und ein Journal, analog zu `run_nli42.py`.

## Stufen

| Stufe | Zieldatum | Ergebnis | Kursbezug |
|---|---|---|---|
| 1 | Mi 04.11. | Graph-Grundgerüst mit Suchschleife und Werkzeugen, lokalem Ablaufprotokoll, CLI-Aufruf; Apertus-Tool-Calling-Smoke; deutscher Quellenindex | SW5, SW7 |
| 2 | Do 05.11. Entwurf, So 08.11. gesperrt | Testset (30 Dev / 60 Test / 20 Spezial) mit erwarteten Ergebnissen, geprüft von Sheyla, gesperrt **bevor** Schwellenwerte kalibriert werden | Evaluation |
| 3 | So 08.11. | Rückfrage, Nachsuche, Budget; MCP-Server und Anbindung | SW6, SW7 |
| 4 | Fr 13.11. | Oberfläche Variante B im Faktencheck-Tab, Unterbrechen/Fortsetzen, Sitzungs-Memory | SW8, Interface |
| 5 | Do 19.11. | Safeguarding, Regression, Evaluation B0/B1/B2, Embedding-Vergleich, Fehleranalyse, Doku | SW9 |
| 6 | So 22.11. | Demo, Offline-Ersatzaufnahme, Präsentation | 23.11. |

Stufe 2 kann parallel zu Stufe 1 laufen. Die Daten sind Ziele; KW45 ist Blockwoche.

**Wenn die Zeit knapp wird, fällt in dieser Reihenfolge weg (Claudes Entscheidung):**
1. Embedding-Vergleich. Es bleibt bei E5.
2. Memory über das Minimum hinaus. Dann gibt es keinen „wie zuletzt“-Knopf.
3. Spezialfälle. Übrig bleiben Mehrdeutigkeit, fehlende Quelle, API-Ausfall und Prompt Injection.
4. Nachsuche nach „nicht belegt“.

Nicht gekürzt werden: Suchschleife mit Quellenfindung und Rückfrage, Belegkontrolle,
Oberfläche B, gesperrtes Testset mit B0/B1, minimaler MCP-Nachweis, Ablaufprotokoll.

## Dem Builder überlassen

- Genaue Graph-Topologie, Knotennamen und State-Felder
- Chunking, Index-Layout, Aggregationsformel für Vorlagenkandidaten, Schwellenwerte (nur aus Dev)
- Prompt-Formulierungen und Werkzeugbeschreibungen. Laut SW6 Folie 8 sind sie selbst ein Prompt.
- Wie Streamlit die asynchronen MCP-Werkzeuge und das Live-Update der Schrittliste bedient
- Form des Ablaufprotokolls im Detail

## Offen außerhalb des Builds

- PR `genai/vector-rag` → `main`. Dieser Branch baut darauf auf.
- Im Coaching klären, ob ein eigener MCP-Server als Nachweis reicht.
- Abgabeform: `intro.html` nennt Präsentation und Notebook; ob ein Repo-Link genügt, ist unklar.
- LangSmith-Konto und gegebenenfalls `LITELLM_API_KEY` für den Embedding-Vergleich.
