# Echter gepaarter NLI-Vergleich auf 42 Dev-Fällen

31.10.2026. **Vollständig abgeschlossen: 42 identische Fälle × drei Varianten,
126 Ergebnisse, null Anfrage-/Formatfehler.** 105 neue HTTP-Anfragen; 15 identische
Eingaben innerhalb des Laufs und sechs vorherige geprüfte Antworten wurden
wiederverwendet. Das autorisierte Limit von 126 tatsächlichen HTTP-Anfragen wurde
eingehalten. Kein Mock und kein Modellwechsel.

## Gemessene Ergebnisse

Apertus-Modell und JSON-Prompt sind in allen Armen konstant. Hauptmetrik ist
Macro-F1 über die drei offiziellen Klassen; Evidence Hit@5 nutzt den offiziellen
Matcher auf Modellzitaten gegen die bereitgestellten Referenzabschnitte.

| Retrievalarm | Macro-F1 | Korrekte Labels | Evidence Hit@5 | Ø Prompttokens | Ø neue NLI-Antwortzeit |
|---|---:|---:|---:|---:|---:|
| baseline_native | 1.0000 | 42/42 | 25/31 (80.6%) | 1624 | 2.39 s |
| vector | 0.8503 | 37/42 | 15/31 (48.4%) | 2412 | 2.89 s |
| vector_hybrid | 0.7226 | 33/42 | 13/31 (41.9%) | 2436 | 2.85 s |

Die Evidence-Metrik hat **31 Gold-Entailment-/Contradictionfälle** im Nenner.
Gold-Neutralfälle werden ausgeschlossen; vorhergesagte Neutralfälle liefern keine
Belege und zählen bei Gold 0/2 als verfehlter Beleg. Prompttoken-Mittelwerte sind
pro Fall/Arm einschliesslich wiederverwendeter Antworten zu verstehen, keine
Addition tatsächlich neu abgerechneter Anfragen. Die Antwortzeiten verwenden nur
neue Antworten, jeweils 35 pro Arm. Es handelt sich um einen lokalen seriellen
Einzellauf, keine stabile Performance-Rangfolge.

Die neue Inferenz verbrauchte laut Server **225 416 Prompttokens und 18 809
Completiontokens**. Keine geschätzten Usage-Werte. Laufzeit der NLI-Phase: rund
4 Minuten 45 Sekunden. Keine Aussage über einen Rechnungsbetrag.

## Sprachübergreifend und gepaart

| Arm | Macro-F1: 27 crosslinguale Fälle | Macro-F1: 15 monolinguale Fälle |
|---|---:|---:|
| baseline_native | 1.0000 | 1.0000 |
| vector | 0.7984 | 0.9348 |
| vector_hybrid | 0.6043 | 0.9348 |

Dense führt gegenüber der Baseline fünf neue Fehler ein, Fusion neun; keine der
beiden Varianten korrigiert einen Baselinefehler auf dieser Stichprobe.
Bei Dense werden drei Entailments und zwei Contradictions als Neutral eingestuft.
Bei Fusion gehen vier Entailments und drei Contradictions in Neutral über; zwei
weitere Entailments werden zu Contradiction. Die nachgelagerte Entscheidungsregel
hat in **keiner der 126 Zeilen** das Rohlabel verändert. Diese Fehler sind folglich
keine Folge einer abweichenden Labelkalibrierung dieses Laufs.

Konkrete Dense-Fehlfälle: `ost-0183` (150 Diensttage), `ost-0414` und `ost-0439`
(EU als wichtigster Partner), `ost-1198` (keine freie Wahl des Dienstes) und
`ost-1235` (Empfehlung, die Initiative abzulehnen). Niedrige Referenzabdeckung im
Kontext ist bei mehreren Fällen eine plausible Erklärung. Ein Referenz-Proxy allein
beweist weder eine Retrievalursache noch die Vollständigkeit des nötigen Belegs.
Die vollständigen Fälle und Konfusionsmatrizen stehen in `real_nli42_analysis.json`.

## Was der perfekte Baselinewert bedeutet

**1.0000 ist hier ein Dev-/Regressionswert, kein Nachweis perfekter Generalisierung.**
Alle Fälle stammen von einem bereits beobachteten Büchleindatum. Alle 42 Claims
haben erkannte Abschnitts-Cues; freie Claims sind nicht enthalten. 25 Labels sind
Entailment, elf Neutral und sechs Contradiction. Die sechs sprachabhängigen
Vote-Titelstrings bezeichnen zwei Abstimmungsvorlagen in drei Sprachen.

Fünf Fallpaare haben identische Aufgaben-Eingaben, Referenzen und Labels. Bei
Deduplikation bleiben 37 Fälle: 22 Entailment, neun Neutral und sechs Contradiction.
Die Referenztexte bilden nur 24 unterschiedliche Gruppen; auch diese sind keine
bewiesenen unabhängigen Inhalts-/Paraphrasenfamilien. Ohne Eingabedubletten:

| Arm | Korrekt auf 37 eindeutigen Eingaben | Macro-F1 |
|---|---:|---:|
| baseline_native | 37/37 | 1.0000 |
| vector | 32/37 | 0.8365 |
| vector_hybrid | 29/37 | 0.7224 |

Kein gewöhnlicher Fall-Bootstrap oder Signifikanzanspruch wird als Beweis eines
allgemeinen Modellvorteils ausgegeben. Diese Daten und ähnliche Vorlagen haben
bereits Entwicklung und Konfigurationsentscheidungen beeinflusst.

Das NLI-Modell erhielt **nur Claim, Claim-Sprache und Büchleinkontext**, keine
Goldlabels oder Gold-Referenzabschnitte. Retrieval erhielt ausserdem den vorgegebenen
Vorlagentitel und das Büchlein in dessen Quellsprache. Das entspricht den hier
modellierten Advanced-Aufgabeneingaben; es ist keine Recherche ohne bekanntes
Quellbüchlein. Label und Referenzstring werden erst danach fürs Scoring verwendet.

Die Baseline setzt ihre cue-/sprecherbasierte Auswahl und ihr natives Budget ein
(Ø 876 E5-Texttokens). Dense und Fusion verwenden je 1536 E5-Texttokens.
Der Vergleich bewertet diese Retrievalpipelines mit gleichem NLI-Prompt, **isoliert
aber nicht allein den Embedding-Effekt**. Der frühere kontrollierte Chunk-/Budget-
Vergleich und die Silver-Belegprüfung bleiben separate Experimente.

## Belegfehler trotz korrekter NLI

Bei sechs der 31 relevanten Baselinefälle verfehlt das Modellzitat den offiziellen
Referenzmatcher. Vier Antworten zitieren bei italienischem Büchlein französischen
Text: `ost-0207`, `ost-0266`, `ost-0273`, `ost-1235`. Das sind Übersetzungen in die
Claim-Sprache statt wortgetreuer Quellzitate. `ost-0472` ist eine deutsche Paraphrase.
`ost-0183` hingegen zitiert nachweislich wörtlich aus dem tatsächlichen Kontext,
verfehlt aber die bereitgestellte Referenzpassage. Ein verfehltes Reference-Hit
beweist deshalb nicht pauschal einen falschen oder erfundenen Beleg.

Diese Beobachtung betrifft den hier verwendeten **JSON-Prompt mit generierten
Zitaten**. Sie ist kein gemessener Fehlernachweis für die bestehende Produktions-
Pipeline mit Passage-IDs und Quelltext-Nachbearbeitung. Der Lauf bildet bewusst
nicht die gesamte optimierte Produktions-NLI oder den Wettbewerbsgesamtscore ab.

## Reproduzierbarkeit, Grenzen und nächste Entscheidung

Quellstand des Runners und des verifizierten eingefrorenen Retrievalcodes:
`6fb6025`. Zusätzlich stehen Quellcode-/Dataset-/PDF-/Kontexthashes in den Reports.
Codex rechnete sämtliche Labels und Evidence-Hits unabhängig nach; Claude führte
vor und nach dem Lauf ein read-only Review durch. Keine Label-Leakage festgestellt.

Ein weiterer **rein lokaler Audit** rekonstruierte die tatsächlichen serialisierten
Request-Bytes durch die originale Promptfunktion mit vollständig ersetzter
Netzwerkmethode. Alle 105 neuen und sechs verwendeten früheren Request-Hashes
stimmen exakt überein; Modell, Temperatur 0.0 und maximal 512 Completiontokens
sind damit auch gegen die protokollierten Bodyhashes geprüft. Keine Inferenz wurde
für diesen Audit wiederholt.

Das Journal persistiert jede Reservierung vor dem Senden und jede Rohantwort vor
dem Parsing. Konnektionswiederholungen zählen gegen den tatsächlichen HTTP-Deckel.
Fehlende/ungültige Labels, Wahrscheinlichkeiten und Belegformate werden als Fehler
behandelt. Systemische Fehler brechen den Lauf ab. Eine kanonische Kampagnendatei
verhindert einen automatischen zweiten Budgetverbrauch mit anderem Ausgabepfad.
Output und Journal werden nicht überschrieben; es gibt kein automatisches Paid-
Resume. Hier war keine Fehler-/Partialfall-Auswertung nötig: alle 42 Paare komplett.

Artefakte unter `track_2a/results/genai/`:

- `nli42_frozen_retrieval.json`: drei eingefrorene Kontexte pro Fall, echte Embeddings.
- `real_nli42.json`: Labels, Metriken, Token-/Zeitwerte, Paare und Fehlerzählung.
- `real_nli42.journal.jsonl`: tatsächlich reservierte Anfragen und Rohantworten.
- `real_nli42_analysis.json`: unabhängige Rechenprüfung, Deduplikation, Fehlfälle.
- `real_nli42_payload_audit.json`: offline geprüfte tatsächliche Requesthashes.

Start aus `track_2a` mit der vorhandenen RAG-Umgebung (bestehende Kampagne und
Ausgabepfade **nicht blind wiederholen**):

```sh
PY=/Users/sheyla/Documents/ChatGPT/GenAI/work/rag-venv/bin/python
$PY scripts/run_nli42.py --dataset data/genai/ost_june_dev.jsonl \
  --report results/genai/nli42_frozen_retrieval.json \
  --env-file /Users/sheyla/Projects/Apertus-Team-Fact-Attack-2026/track_2a/.env \
  --reuse results/genai/real_nli_access_smoke.json --max-requests 126 \
  --output results/genai/real_nli42.json
# Rein offline, ohne Zugangsdaten oder Netzwerkanfrage:
$PY scripts/audit_nli42_payloads.py
```

**Entscheidung:** Die bestehende Retrieval-Standardstrategie wird nicht ersetzt.
Für einen weiteren Qualitätsnachweis braucht es neue, unabhängig verschlossene
Fälle aus anderen Vorlagen/Terminen, insbesondere echte freie Claims, sowie
menschlich geprüfte Belege. Die belegtreue Produktionsausgabe mit Passage-IDs ist
separat zu prüfen. Docker/Python 3.12 und Streamlit bleiben offene Integrationsschritte.
