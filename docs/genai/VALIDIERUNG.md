# Validierung des ersten Vektor-RAG-Prototyps

31.10.2026, macOS ARM64, Python 3.10.13 in isoliertem venv.

- Vollständige Suite: **186 Tests bestanden**, zusätzlich 6 Subtests; 5 bestehende
  Deprecation-Warnungen der PDF-Bibliotheken. `python -m pytest track_2a/tests -q`.
- Darin 4 neue Tests mit echter lokaler Qdrant-Datenbank und Test-Embeddings:
  Retrieval/Quellen/Sprecher, Persistenz/Quellenänderung, ungültige Vektoren/leere Daten,
  Integration in den Prüfpfad. Keine Modell-API erforderlich.
- Echter Embedding-Funktionstest mit `intfloat/multilingual-e5-small`, Revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 56 Chunks auf dem bestehenden
  Büchlein `2026-06-14_de.pdf`. Report unter `track_2a/results/genai/`.
- Zusätzlicher CLI-End-to-End-Test: `predict --strategy vector_hybrid --mock --json`
  mit echtem Embedding-Modell, echtem PDF und gemockter NLI. Gültiger Output mit Seitenbelegen.
  Das Mock-Urteil ist keine Qualitätsmessung und wird nicht als Ergebnis publiziert.
- Kein kostenpflichtiger NLI-Aufruf; kein Nachweis eines verbesserten Scores.
- Docker und Streamlit-Integration der neuen Optionen noch nicht geprüft/implementiert.

Wichtige lokal geprüfte Versionen: qdrant-client 1.20.0, sentence-transformers 6.2.0,
transformers 5.20.2, torch 2.14.1, rank-bm25 0.2.2, pypdf 6.21.4,
PyMuPDF 1.28.2. Dies ist nicht die unveränderte Python-3.12-Wettbewerbsumgebung:
Die dort gepinnten Kernanforderungen bleiben unangetastet; Container-Reproduktion
ist ein eigener noch offener Integrationsschritt.

## Fortsetzung: gepaarter Dev-Vergleich

191 Tests + 6 Subtests in der vollständigen Suite bestanden. Danach wurde der
NLI-Evidence-Nenner auf Gold-Entailment/Contradiction beschränkt; alle vier
Evaluations-Tests inklusive des neuen Nenner-Tests bestanden.
Zwei echte lokale Embedding-Läufe mit 28 Fällen × 6 Armen × 2 Budgets abgeschlossen.
Keine Apertus-API-Anfragen; strikte Mock-Abweisung zusätzlich am CLI geprüft.
Details und Einschränkungen: [ERGEBNISSE_DEV.md](ERGEBNISSE_DEV.md).

## Mehrsprachige Fortsetzung

Vollständige Suite: **202 Tests + 6 Subtests bestanden**, fünf bestehende
PDF-Deprecation-Warnungen, dieselbe Python-3.10-Umgebung. Geprüft wurden unter
anderem unveränderte Quelllabels, Hash-Abweisung bei Datasetdrift, getrennte
claimweise Annotationen trotz gemeinsamer Referenz, n-Gramm-Grenzen, gepaarte
Fallidentität, Clustergewichtung und Cleanup bei Metadatenfehlern.

Zwei neue echte Retrieval-Läufe mit je 42 Fällen × sechs Armen, einschliesslich
27 crosslingualer Fälle. Zwei Chunkkonfigurationen und tatsächlich neuer
Indexaufbau separat erfasst. Keine echten NLI-Aufrufe, keine neuen Goldseiten.
Details: [ERGEBNISSE_CROSSLINGUAL_DEV.md](ERGEBNISSE_CROSSLINGUAL_DEV.md).

## Quellzitat-/Parser-Fortsetzung

**209 Tests + 6 Subtests bestanden**. Neu geprüft: exakte Quellzitate und Offsets,
Abweisung geänderter Labels/ungeprüfter Annotationen, falsche Kontexthashes,
Normalisierung harter/weicher Worttrennungen, Erhalt suspensiver Bindestriche und
explizite gepaarte Fallauswahl. Weiterhin fünf bestehende PDF-Warnungen.

Zwölf Codex-/Claude-geprüfte Dev-Silver-Zitate wurden in beiden PDF-Extraktions-
pfaden bestätigt. Neuer echter Retrieval-Lauf auf denselben zwölf Fällen nach
Parser-/Quick-Pfad-Korrektur, Cache v9. Keine echten NLI-Aufrufe.
[Ergebnisse und Grenzen](ERGEBNISSE_BELEGPRUEFUNG.md).

## Vorhandener Zugang / echte NLI

Vollständige Suite: **218 Tests + 6 Subtests bestanden**, fünf bestehende
PDF-Deprecation-Warnungen. Neue Tests prüfen den echten Transport-Requestdeckel
inklusive Reconnect, fsync vor dem Senden und Abweisung fehlender/ungültiger JSON-
Labels. Danach wurden Neutral-Evidence-Darstellung und Token-/Anfragezahlen direkt
an den gespeicherten Antworten geprüft, ohne neue Inferenz.

Realer Smoke erfolgreich: elf tatsächliche POST-Anfragen, elf Antworten, zwölf
gepaarte Zeilen mit einer identischen wiederverwendeten Antwort, kein Mock und
bestätigtes Servermodell. Artefakte wurden auf enthaltene Zugangsschlüssel geprüft:
keine gefunden. Details: [APERTUS_ZUGANG_UND_SMOKE.md](APERTUS_ZUGANG_UND_SMOKE.md).

## Echter gepaarter 42-Fälle-Lauf

**224 Tests + 6 Subtests bestanden**; fünf bestehende PDF-Deprecation-Warnungen.
Neu geprüft: harte Grenze bei HTTP-Versuch 126, Ausschluss eines fehlgeschlagenen
Falls für sämtliche Arme, doppelte Paar-IDs, inkompatible Cachequellen, fehlende
42er-Paarung und striktes NLI-Antwortschema.

Realer Lauf: 42 vollständige Paare, 126 Zeilen, 105 neue Anfragen, 15 Wiederverwendungen
innerhalb des Laufs und sechs geprüfte frühere Antworten. Null Anfrage-/Schemafehler.
Unabhängige sklearn- und offizielle Evidence-Nachrechnung stimmen überein. Rein
offline wurden sämtliche 105 neuen und sechs verwendeten früheren HTTP-Payloadhashes
bytegenau gegen rekonstruierte Eingaben geprüft. Keine zusätzlichen Modellaufrufe.
[Ergebnisbericht](ERGEBNISSE_NLI42.md).

## UI und Python 3.12

**241 Tests + 6 Subtests bestanden**, CPython 3.12.13 auf macOS ARM64, in einer
zweiten neu aus vollständigem Hash-Lock installierten Umgebung. Alle 89 Pakete
stimmen mit `requirements-genai312-macos-arm64.lock.txt` überein. Unveränderte
Kernanforderungen plus RAG-Zusätze. Acht Warnungen (fünf PDF, drei Fork-/Thread-Warnungen
in den Tests). Docker/Python-3.12.6-Linux wurde nicht reproduziert.

Reale Streamlit-AppTests und Browserprüfung erfolgreich. Sechs Produktions-IDs-
Fälle sichern Originalquellenbelege trotz übersetzter Modellzitate; echtes lokales
E5/Qdrant in beiden Vektormodi zusätzlich mit injizierter NLI geprüft. Null neue
kostenpflichtige NLI-Anfragen in dieser Phase. [Start und Grenzen](UI_UND_PYTHON312.md).

## Agentenschicht Stufe 1

Vollständige Suite: **495 Tests bestanden**, 112 Subtests, 8 bestehende Warnungen (fünf
Deprecation-Warnungen der SWIG-Typen von PyMuPDF, drei Fork-Warnungen in `tests/test_page_pool.py`),
CPython 3.12.13 auf macOS ARM64, Python aus `.venv312` mit dem Hash-Lock. Das sind die 286 Tests
von vor der Agentenschicht (inklusive der Byte-Identität der Apertus-Requests in
`tests/test_apertus_client.py`) plus 209 neue in `tests/test_agent_*.py`;
`requirements.txt` und der Wettbewerbspfad sind unverändert. Docker/Python-3.12.6-Linux wurde
nicht reproduziert.

Echte Läufe mit hartem Anfrage-Deckel und Journal, alle nur Beobachtung und keine
Qualitätsmessung:

- **Tool-Calling-Smoke:** 6 von 6 Anfragen. Nativ 0/3 gültig (HTTP 400, der Server hat kein
  Auto-Tool-Choice), JSON 3/3 gültig, 2/3 mit dem erwarteten Werkzeug; Standard bleibt `json`.
  `agent_tool_smoke.json`.
- **Drei Ende-zu-Ende-Läufe der CLI:** 9 Anfragen (4, 4 und 1; Deckel je 12, genehmigt waren
  höchstens 36). Mietrecht-Behauptung: `gestuetzt` auf der Eigenbedarf-Vorlage, erwartet war
  `rueckfrage`, der Agent hat bei zwei gleichauf liegenden Vorlagen geraten. Zivildienst-
  Behauptung: `gestuetzt` auf der richtigen Vorlage. Euro-Behauptung: `quelle_fehlt`, aber ohne
  dass der Agent den Index befragt hat. `agent_stage1_runs.json` mit Protokollen und Journalen
  unter `agent_stage1_traces/`.
- Die Artefakte (Ergebnisse, Journale, Protokolle) wurden auf Schlüsselmaterial geprüft: keines
  gefunden.
- In beiden `gestuetzt`-Läufen verwarf die damalige Belegkontrolle eines von fünf Zitaten zu Unrecht:
  einen echten Textblock des PDF, den sie nur gegen den Seitentext prüfte. Offline mit der
  korrigierten Kontrolle nachgeprüft (gespeicherte Zitate, keine Anfrage): je 5 von 5 gültig.

Nach den Korrekturen des Abschluss-Reviews (Belegkontrolle mit Textblöcken, abgeschnittener Denkanteil,
Protokolldaten, CLI-Aufräumen): Vollständige Suite **517 Tests bestanden**, 131 Subtests, dieselben
8 Warnungen. Mit einer Simulation der Wettbewerbsumgebung (Pakete, die `requirements-lock.txt` nicht
enthält, wie httpx, LangGraph, LangChain und Qdrant, werden beim Import blockiert) laden alle
`tests/test_agent_*.py` und werden übersprungen, wo die Pakete fehlen: 231 Tests, 170 übersprungen,
keine Fehler. Ein echter Lauf mit Docker und `requirements-lock.txt` steht weiter aus.

Ohne Qualitätsaussage: Es gibt noch kein gesperrtes Testset und keinen Vergleich mit dem
bewährten Pfad. Details, Schrittlisten und Grenzen: [AGENT_STUFE1.md](AGENT_STUFE1.md).

## Testset der Agentenschicht gesperrt

Am 13.11.2026 gesperrt: 110 Fälle. 37 davon gehören zu Dev (30 Haupt-, 7 Spezialfälle), 73 zu Test (60 Haupt-,
13 Spezialfälle).
- `scripts/check_testset.py`: 110 Fälle, 0 Probleme.
- **Sheylas Blindprüfung:** Das erste Urteil stimmte bei 82 von 110 Fällen mit dem Entwurf überein. Die 28
  übrigen hat sie nach dem Aufdecken korrigiert.
- **Codex als blinder Zweitprüfer:** Er stimmt bei 110 von 110 Fällen mit den Labels überein, auch bei diesen 28
  (`results/genai/testset_label_check_codex.jsonl`).
- **Zugriff:** `load_testset("dev")` liefert 37 Fälle; der Testteil bleibt bis zum Konfigurationsfreeze
  gesperrt.

Vollständige Suite auf dem Stand von Stufe 3 (Tasks 1–7 mit den Korrekturen des Gesamtreviews): **842 Tests
bestanden**, 158 Subtests, dieselben 8 Warnungen.

Details und Grenzen: [TESTSET.md](TESTSET.md), Abschnitt „Gesperrt“.

## Agentenschicht Stufe 3

13.11.2026: Wächter mit kalibrierten Schwellen (Dev-Teil des gesperrten Testsets), Nachsuche mit Budget 5 / 1 / 2 / 12,
eigener MCP-Server und fünf gedeckelte echte Läufe.
- Vollständige Suite: **850 Tests bestanden**, 158 Subtests, dieselben 8 Warnungen. Kein MCP-Server-Prozess blieb
  zurück.
- Echte Läufe: 5 Läufe, 17 Anfragen von höchstens 60 (Journalzeilen 2 / 5 / 4 / 4 / 2). 2 von 5 wie erwartet
  (Nachsuche mit zweiter Prüfung, MCP mit echtem Apertus); in den anderen 3 fragte das Modell selbst zurück, bevor
  der Wächter eingriff, zweimal unnötig.
- Nativer Tool-Calling-Smoke (04.11.): `tool_choice="required"` HTTP 400 in 3 von 3, `json` bleibt Standard.

Ohne Qualitätsaussage: Einzelläufe sind keine Messung; der Testteil des Testsets ist weiter gesperrt. Details,
Befunde und offene Punkte: [AGENT_STUFE3.md](AGENT_STUFE3.md).

## Agentenschicht Stufe 5: Evaluation B0/B1/B2

14.11.2026: Konfigurationsfreeze bei `7bca92b` (Konfig-Hash `c60b30bce0b6…`). Danach wurde der Testteil des
gesperrten Testsets einmal je Arm ausgewertet.

**Test (73 Fälle, 64 beantwortbar), Macro-F1 streng:**

| Arm | F1 streng | Fehlurteile | Enthaltungen | Anfragen |
|---|---|---|---|---|
| B1 (Agent) | 0.853 | 9 | 1 | 380 |
| B2 (ohne Nachsuche und Belegkontrolle) | 0.812 | 11 | 2 | 321 |
| B0 (bewährter Pfad, Orakel-Büchlein) | 0.783 | 14 | 0 | 70 |

- **Unterschiede nicht gesichert:** B1 gegen B0 unterscheiden sich in 8 zu 4 Fällen, B1 gegen B2 in 3 zu 0.
- **Beleggültigkeit** ist 1.000 in allen Armen.
- **Regression** auf 42 Dev-Fällen bestanden: B0 und Agent 42 von 42, kein geänderter Fall.
- **Wichtigste Befunde:**
  - Prompt Injection: 0 von 2 auf Test in allen Armen, das Urteil stimmt jeweils mit der eingeschleusten
    Anweisung überein. Ohne Kontrolllauf ist das nicht von gewöhnlichen Fehlern zu trennen. Über alle drei Fälle
    mit Dev: B1 0 von 3, B0 und B2 je 1 von 3. Planziel nicht erreicht.
  - 19 unberechtigte gegenüber 3 berechtigten Rückfragen, wahrscheinlich verursacht durch die Wächter-Schwelle (Hypothese, kein Kontrolllauf).
  - Kein `quelle_fehlt` bei Themen ohne Abstimmung.
  - Die Teilaussagen-Frage wählte das Modell nie.
- **Bezahlte Anfragen der Stufe:** 1376. Jeder Lauf hatte ein eigenes OK mit Deckel; kein Deckel wurde erreicht.
- **Vollständige Suite:** **1119 Tests bestanden**, 413 Subtests, dieselben 8 Warnungen (vor Stufe 5: 994).

Grenzen: kleine Fallzahl, ein Lauf je Arm, ein Modell, Temperatur 0 ohne Determinismus (2 von 67 ersten
Prüfungen schwankten bei gleicher Eingabe). Zahlen, Fehleranalyse, Integrität des Testteils (auch der Vorfall in
Task 2) und Kosten: [EVALUATION_AGENT.md](EVALUATION_AGENT.md).
