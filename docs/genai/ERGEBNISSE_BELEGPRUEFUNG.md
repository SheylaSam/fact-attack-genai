# Quellenprüfung und Korrektur der Seitentexte – 31.10.2026

## Tatsächliche Quellenarbeit

Zwölf bestehende offizielle Dev-Fälle wurden mit konkreten Quellzitaten annotiert:
alle sechs Contradiction-Fälle des Juni-Ausschnitts plus sechs Entailment-Fälle,
zusammen alle neun Sprachpaare (neun crosslingual, drei monolingual). Auswahl vor
Berechnung der neuen Belegmetriken, kein neuer unabhängiger Test. Keine Neutral-
Abwesenheitslabels, keine Änderungen der ursprünglichen NLI-Labels.

Codex hat die Quelltexte und elf gerenderte physische PDF-Seiten geprüft; Claude
Code hat jeden Claim, die Sprecherzuordnung, Negation, Quellzitate und Zeichen-
positionen unabhängig am Text gegengeprüft. Alle zwölf wurden akzeptiert; drei
haben dokumentierte Einschränkungen:

- `ost-0078`: leichte kausale Zusammenziehung zweier Massnahmen im Originalclaim.
- `ost-0439`: Sprecherzuordnung kommt aus Überschrift und Seitenkontext.
- `ost-1232`: Einschränkung der Immigration wird explizit befürwortet; der andere
  Teil des Claims wird nur indirekt widerlegt. Sprecherzuordnung aus Seitenkontext.

Die Auswertung enthält deshalb zusätzlich die neun Fälle ohne diese Einschränkungen.
Datei: `track_2a/data/genai/evidence_silver12.jsonl`. Kennzeichnung ist ausdrücklich
**AI-reviewed Dev/Silver**, `human_reviewed=false`, `independent_test=false`.
Die separate menschliche Prüfliste bleibt unverändert ungeprüft. Keine Übernahme
von Silverdaten in `gold_evidence_pages` des offiziellen Datensatzes.

Der Validator prüft Dataset-, Claim- und Büchleinhashes, unveränderte offizielle
Labels, abgeschlossene unabhängige KI-Prüfung, physische Seitennummern, Länge und
exakte Zeichenpositionen aller Zitate in PyMuPDF. Jedes Zitat muss zusätzlich in
der Projekt-PDF-Extraktion nach begrenzter Normalisierung bestätigt werden.
Ein Zeilenumbruch nach einem expliziten Soft-Hyphen wird dabei gezielt zusammen-
geführt. Die offizielle Fuzzy-Evaluation bleibt unverändert.

## Gefundener und behobener Implementierungsfehler

Ganze Seiten verloren beim Zusammenfassen von Whitespace ihre Zeilenumbrüche,
bevor Worttrennungen aufgelöst wurden. Dadurch wurde beispielsweise `Parla-` +
Zeilenumbruch + `ment` zu `Parla- ment`. Chunkbasierte Satzaufteilung benutzte
bereits eine andere Normalisierung. Der Quick-Pfad hatte dieselbe Abweichung.

Ganze Seiten und Quick-Passagen verwenden jetzt dieselbe gemeinsame Normalisierung.
Zusätzlich werden explizite Soft-Hyphen-Zeilenumbrüche aufgelöst. Suspensive Formen
wie `Ein-` + Zeilenumbruch + `und Ausfuhr` bleiben `Ein- und Ausfuhr`, und echte
Zusammensetzungen wie `E-Voting` bleiben erhalten. Der Parsercache ist auf **v9**
angehoben; alte Ergebnisse werden nicht überschrieben und alte v7/v8-Cachetexte
nicht als neue Parserausgabe wiederverwendet.

## Gemessener Vorher-Nachher-Vergleich

Dieselben zwölf Fälle, sechs Arme, 384/64-Chunks, k=20, maximal 1536 E5-Texttokens.
Die native Baseline behält ihr eigenes Kontextbudget. Nach der Korrektur wurde die
Suche mit echten lokalen E5-Embeddings neu ausgeführt (72 Zeilen). Historische
Vergleichskontexte stammen aus dem gespeicherten 42-Fälle-Lauf mit denselben
Parametern; die neue Auswertung filtert daraus genau diese zwölf Fall-IDs.

| Arm | Ausgewählte Belegseite vorher | danach | Vollständiges Zitat vorher | danach |
|---|---:|---:|---:|---:|
| baseline_native | 11/12 | 12/12 | 5/12 | 12/12 |
| baseline_budgeted | 10/12 | 10/12 | 4/12 | 10/12 |
| bm25_pages | 3/12 | 2/12 | 1/12 | 2/12 |
| bm25_chunks | 4/12 | 4/12 | 1/12 | 4/12 |
| vector | 8/12 | 5/12 | 2/12 | 5/12 |
| vector_hybrid | 5/12 | 4/12 | 1/12 | 4/12 |

Ohne die drei Review-Einschränkungen enthält die native Baseline nach der Korrektur
in allen neun Fällen das ausgewählte vollständige Zitat; Dense in drei, BM25 auf
Chunks in drei und Fusion in zwei. Das sind kleine beobachtete Silver-Teilmengen,
keine Rangfolge mit statistischer Absicherung.

**Interpretation:** Die Korrektur beseitigt nachgewiesene Extraktionsartefakte.
Die bessere Quote-Erkennbarkeit ist teilweise ein Normalisierungseffekt und kein
bewiesener semantischer Qualitätsgewinn. Die neuen Texttokens verändern auch
Ranking und Chunkgrenzen; Dense findet auf dieser Stichprobe nach der Änderung
weniger der ausgewählten Belegseiten. Deshalb kein genereller Gewinn für jede
Suche, keine automatische Übernahme einer neuen Retrievalstrategie.

Die früheren 128/32-Kontexte wurden ebenfalls gegen die Silverzitate geprüft:
`silver12_chunk128.json` ist ausdrücklich eine **historische** Diagnose der alten
Textaufbereitung, kein neuer v9-Retrievallauf. Da kurze Chunks lange Zitate seltener
am Stück enthalten, ist vollständiges Zitat-Hit zwischen Chunkgrössen kein fairer
isolierter Qualitätsvergleich.

## Messgrenzen

Ein Seiten-Hit bedeutet nur, dass eine ausgewählte Quellseite im Kontext enthalten
ist; eine andere Passage derselben Seite kann genügen. Vollständiges Zitat-Hit
verlangt ein ganzes normalisiertes Zitat in einer einzelnen Passage. Verteilte
Belege über mehrere Chunks und ebenfalls gültige andere Quellseiten können dadurch
als Fehlfall erscheinen. Die Annotationen sind **nicht erschöpfend**. Deshalb kein
unqualifiziertes Recall, keine Gold-/Testbehauptung und kein NLI Macro-F1.

Die Reports enthalten Annotations-, Dataset-, Quellcode- und Retrievalreporthashes,
Beleg-Hits vor/nach Budget und separate Gruppen nach Sprache und Review-Caveat.
Kontexttexte müssen ihre gespeicherten Text-Hashes erfüllen und einer ursprünglichen
Passage oder deren unverändertem Präfix entsprechen. Hashes sichern Reproduzierbarkeit
und versehentliche Änderungen ab; sie sind keine externe Signatur.

## Startbefehle

Aus `track_2a`, mit der bestehenden RAG-Umgebung und dem gecachten Modell:

```sh
export HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache
export HF_HUB_OFFLINE=1
PY=/Users/sheyla/Documents/ChatGPT/GenAI/work/rag-venv/bin/python
$PY -m src.rag_evaluation --dataset data/genai/ost_june_dev.jsonl \
  --case-ids ost-0000 ost-0078 ost-0121 ost-0183 ost-0439 ost-0472 \
             ost-1097 ost-1198 ost-1208 ost-1232 ost-1235 ost-1444 \
  --top-k 20 --budget 1536 --save-hits \
  --output results/genai/silver12_retrieval_dehyphenated.json
$PY -m src.evidence_silver --dataset data/genai/ost_june_dev.jsonl \
  --annotations data/genai/evidence_silver12.jsonl --booklets data/booklets \
  --report results/genai/silver12_retrieval_dehyphenated.json \
  --output results/genai/silver12_dehyphenated.json
```

Quellstand der neuen Retrievalmessung: `5832b11`; exakte Parser-/Textnormalisierungs-
Hashes sind zusätzlich im Report gespeichert. Vollständige Suite: **209 Tests plus
6 Subtests bestanden**, fünf bestehende PDF-Deprecation-Warnungen, Python 3.10.

## NLI-Zugang

Weiterhin keine echte NLI-Inferenz. Erneute lokale Prüfung fand keine ausgefüllte
`.env` im GenAI-Projekt, Arbeitsworktree oder ursprünglichen Klon; dort sind nur
`.env.example`-Vorlagen vorhanden. Der aktuelle Prozess lädt keinen API-Schlüssel.
Das sagt nichts darüber aus, ob andernorts bereits ein gültiger Zugang vorhanden ist.
Das Projekt erwartet `API_KEY` oder `LLM_API_KEY` für seinen Apertus-/CSCS-Endpunkt.
Ein Dateipfad zu einer vorhandenen Konfiguration reicht für die weitere Prüfung;
keine Schlüsselwerte in Chat oder Git übernehmen.
