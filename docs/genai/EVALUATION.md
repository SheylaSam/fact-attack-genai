# Gepaarte Dev-Evaluation

Implementiert am 31.10.2026 nach zwei unabhängigen Claude-Code-Reviews.
Alle bisherigen Daten sind beobachtete Dev-/Regressionsdaten. Das betrifft auch
den früheren September-Holdout. Neue Testdaten sind noch nicht vorhanden;
[TESTDATEN_PROTOKOLL.md](TESTDATEN_PROTOKOLL.md) beschreibt Gruppierung und Verschluss.

## Reproduzieren

Aus `track_2a`, in der vorhandenen Umgebung mit `requirements-rag.txt`:

```sh
export HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache
export HF_HUB_OFFLINE=1
PY=/Users/sheyla/Documents/ChatGPT/GenAI/work/rag-venv/bin/python
$PY -m src.rag_evaluation --output results/genai/paired_dev_retrieval.json
$PY -m src.rag_evaluation --budget 768 --output results/genai/paired_dev_budget768.json
```

Die Offline-Einstellung setzt bereits gecachte Modellgewichte voraus. Auf einer
neuen Maschine zuerst Modell herunterladen; Modellrevision ist im Evaluator gepinnt.
Keine Daten oder Claims gehen an einen Embedding-Dienst. Ein Prozess pro Indexordner.
Die Report-Dateien enthalten Dataset-/PDF-/Quellcodehashes, Git-Commit, Modellrevision,
Indexfingerprint, Index-Reuse und pro Fall die verwendeten physischen Seiten.

## Vergleichsarme und Metriken

- `baseline_native`: bisherige Hybridpipeline inklusive cue-/sprecherbasierter
  Auswahl mit ihrem ursprünglichen Kontext. Keine zusätzliche Budgetkürzung.
- `baseline_budgeted`: dieselbe Auswahl, gemeinsames E5-Textbudget. Cue-Teile
  bleiben in ihrer Produktionsreihenfolge; keine zusätzliche top-k-Seitenkürzung.
- `bm25_pages`: lexikalische Suche auf Seiten, ohne cue-/Sprecherheuristiken.
- **Kontrollierte Ranking-Ablation**: `bm25_chunks`, `vector`, `vector_hybrid`
  auf identischen 384/64-E5-Chunks, gleicher top-k-Grenze und gleichem maximalen
  E5-Textbudget. Keine Sprecherheuristiken in einem dieser drei Arme.

Die zwei Läufe vergleichen 1536 und 768 E5-Texttokens bei k=5. Gleiche Obergrenze
bedeutet nicht identische tatsächlich verbrauchte Tokens: kürzere Treffer können
unterhalb des Budgets bleiben. Promptformatierung wird nicht mitgezählt, E5 ist
nicht der Apertus-Tokenizer. Der Vergleich ist eine Budget-/Chunk-Ablation,
kein isolierter Vergleich identischer NLI-Promptkosten.

Die vorhandenen Referenztexte sind grössere Abschnitte, keine manuell geprüften
Belegspans. `reference_hit_at_k` und `reference_hit_at_5` verwenden den offiziellen
Fuzzy-Quote-Matcher als **Referenztreffer-Proxy**, nicht echtes Evidence Hit@5 eines
NLI-Modells. Einheitengrösse und dessen 5000-Zeichen-Grenze beeinflussen den Proxy.
Vergleiche deshalb vorrangig die drei Arme auf denselben Chunks.
`reference_token_coverage` ist die Abdeckung einzigartiger E5-Token-IDs des
Referenzabschnitts, kein Recall von Belegspans.

Für manuell annotierte positive physische `gold_evidence_pages` berechnet der
Evaluator zusätzlich echten Page-Hit@k und Page-Recall@k (deduplizierte Seiten,
Recall = gefundene Goldseiten / Goldseiten). Ohne solche Annotationen stehen diese
Werte bewusst auf `null`; `gold_page_count` nennt die Zahl annotierter Fälle.
Der Demo-Datensatz hat keine solche Annotation. Auch die automatische Ableitung
aus Referenzähnlichkeit würde keine unabhängigen Goldlabels erzeugen.

Aufteilung in `cue`/`free` erfolgt reproduzierbar mit `claim_part`; das sind erkannte
Cue-Muster, keine zusätzliche menschliche Datensatzannotation. Sprachpaare werden
separat berichtet. Kleine Gruppen tragen `small_sample: true`.

Modellladen und PDF-/Index-Setup stehen separat von den warmen Suchzeiten.
`index_reused` unterscheidet Wiederöffnung von Aufbau. Dense-Warmup nutzt keine
Labels. Die Baseline-Zeit enthält zusätzlich Pipeline-Vorverarbeitung/Numerik und
ist kein reiner Geschwindigkeitsvergleich mit den Retrievern.

## NLI: strikt getrennt, derzeit nicht gemessen

Zugang geprüft: kein API-Schlüssel vorhanden, Client wäre Mock. Kein echter
Apertus-Aufruf wurde ausgeführt. Der Evaluator erzeugt **keine Mock-Qualitätsscores**;
`--nli` bricht bei Mock/fehlendem Schlüssel vor dem Modellladen ab.

Nach lokaler Konfiguration von `LLM_API_KEY` (nie in Chat oder Git ablegen):

```sh
$PY -m src.rag_evaluation --nli --limit 2 --max-nli-calls 12 \
  --output results/genai/paired_nli_access_smoke.json
```

Das sind höchstens zwölf logische NLI-Aufrufe für zwei identische Fälle × sechs
Arme, ein Zugangstest und keine belastbare Qualitätsstudie. Der Deckel umfasst
keine internen Retry-/Repair-Anfragen des bestehenden Clients. Für grössere Läufe
Fallzahl und Deckel bewusst festlegen. Ein Fehler oder Modellwechsel verwirft die
unvollständige Vergleichsausgabe; dann zuerst Zugang/Fehlerursache beheben.

Alle Arme verwenden denselben `ApertusClient.infer`-JSON-Prompt und dieselbe
Modellkonfiguration. Das ist ein kontrollierter NLI-Vergleich, bildet aber nicht die
gesamte optimierte Produktions-NLI inklusive aller Heuristiken nach. Der Report
liefert dann NLI Macro-F1, offizielles Evidence Hit@5 auf Modellzitaten, tatsächliche
Prompt-/Completiontokens und NLI-Zeiten. Ohne `--nli` bleiben diese Metriken `null`.
