# Start: Vektor-RAG nach dem Coaching

Stand: 31.10.2026. Entwicklungsbranch `genai/vector-rag`, Ausgangspunkt `534e4ef`.

## Ziel und Einordnung des Feedbacks

Laut weitergeleiteter Nachricht aus dem Coaching sollen eine Vektordatenbank und
Embeddings integriert und auf eine Verbesserung des Scores untersucht werden.
Als zusätzliche Option wurde ein anderes Modell erwähnt. Wir ergänzen deshalb
zunächst die Suche; Apertus bleibt für die NLI-Prüfung erhalten. Ein grösserer
Agentenumbau und Training sind nachgelagerte Experimente.

Eine Verbesserung ist eine Hypothese. Die bisherige Abschnittsauswahl ist bereits
sehr stark; eine neue Suche kann schlechter sein und wird erst nach Messung übernommen.

## Implementierter erster Schritt

- Optionales `intfloat/multilingual-e5-small` als mehrsprachiges Embedding-Modell.
- Qdrant lokal im Prozess, optional auf Disk; kein Server oder Cloudkonto nötig.
- Tokenbasierte Chunks (384 Tokens, 64 Überlappung), Originaltext und PDF-Seite bleiben erhalten.
- Korrekte E5-Präfixe `query: ` und `passage: `, normalisierte Vektoren und Cosinus-Suche.
- `vector`: reine Vektorsuche. `vector_hybrid`: Reciprocal Rank Fusion aus Vektor- und BM25-Rängen.
- Explizite Integration in `ClaimVerificationEngine` und die vorhandene CLI.
- Der Standard `hybrid` (bisheriger Name für BM25 plus Vorlagentitel/Abschnittsauswahl) bleibt unverändert.
- Vektorvarianten umgehen die cue-basierte Abschnittsauswahl bewusst; andernfalls würde
  diese in den meisten Wettbewerbsfällen die neue Suche umgehen.
- Keine LLM-Übersetzung im Vektorpfad: direkte mehrsprachige Embeddings. Sprecherfilter und
  vorhandene Belegnachbearbeitung bleiben erhalten.
- Indexschlüssel enthält Quelltext, Seiten-/Abschnittsmetadaten, Modellrevision, Chunkparameter
  und Bibliotheksversion. Unvollständiger Index erzeugt einen klaren Fehler statt stiller Wiederverwendung.
- Qdrant/Embedding-Abhängigkeiten werden nur im optionalen Experimentpfad geladen.

`src/vector_rag.py` bietet einen Suchvergleich ohne Apertus-Anfragen.
`requirements-rag.txt` ergänzt die Kernanforderungen. CLI, Python-API und eine explizite
Vektor-Opt-in-Auswahl in Streamlit sind vorhanden. Für macOS ARM64 ist Python 3.12
mit einem vollständigen Hash-Lock geprüft; der Docker-RAG-Build bleibt offen.
Aktuelle Startbefehle: [UI_UND_PYTHON312.md](UI_UND_PYTHON312.md).

## Lokal starten

Aus dem Repository-Root mit Python 3.12, wie für den Bestand vorgesehen:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r track_2a/requirements.txt -r track_2a/requirements-rag.txt
cd track_2a
```

Beim ersten Aufruf wird das öffentliche Embedding-Modell heruntergeladen. Alle weiteren
Berechnungen erfolgen lokal; Claim-Texte werden nicht an einen Embedding-Dienst gesendet.
Für einen persistenten Index ist jeweils ein Prozess pro Indexverzeichnis vorgesehen.

```sh
python -m src.vector_rag \
  --booklet data/booklets/2026-06-14_de.pdf \
  --claim 'Die Initiative begrenzt die ständige Wohnbevölkerung vor 2050 auf zehn Millionen.' \
  --top-k 5 --index-dir .rag-index \
  --output results/genai/my_retrieval_smoke.json
```

Echte Embeddings plus NLI-Mock (nur Funktionstest):

```sh
RAG_INDEX_DIR=.rag-index python -m src predict \
  --booklet data/booklets/2026-06-14_de.pdf \
  --claim 'Die Initiative begrenzt die Wohnbevölkerung.' \
  --strategy vector_hybrid --mock --json
```

Für eine echte NLI-Auswertung `--mock` entfernen und den bereits vorhandenen
API-Zugang konfigurieren. Fehlender API-Schlüssel aktiviert im Bestand einen Mock:
vor Qualitätsmessungen zwingend prüfen, dass echte Inferenz aktiv ist.

Für Batchvergleiche auf derselben vorbereiteten Fall-Datei:

```sh
python -m src run --input cases.jsonl --output baseline.jsonl --strategy hybrid --workers 1
RAG_INDEX_DIR=.rag-index python -m src run --input cases.jsonl --output dense.jsonl --strategy vector --workers 1
RAG_INDEX_DIR=.rag-index python -m src run --input cases.jsonl --output fused.jsonl --strategy vector_hybrid --workers 1
```

Modellname via `RAG_EMBEDDING_MODEL`, Revision via `RAG_MODEL_REVISION`, Gerät via
`RAG_DEVICE` (Standard CPU). Die aktuelle Encoderklasse erwartet E5-kompatible Präfixe;
für andere Modellfamilien einen passenden Encoderadapter implementieren. Indizes und
Modellgewichte bleiben lokal. Bei direkter Python-Nutzung `engine.close()` im `finally`
aufrufen, um Qdrant-Dateisperren freizugeben.

## Was bereits geprüft wurde

Der committed Suchreport `track_2a/results/genai/vector_retrieval_smoke.json` ist ein
Funktionstest an einem bestehenden deutschen Büchlein mit echten E5-Embeddings.
Er dokumentiert Modellrevision, Chunkzahl, Dokumenthash, Treffer und Suchzeiten.
Das ist weder ein Holdout noch ein Nachweis besserer Klassifikation.

Offline-Tests verwenden echte lokale Qdrant-Indizes mit injizierten Testvektoren.
Sie prüfen Quellenzuordnung, Sprecherfilter, Indexwiederverwendung, geänderte
Quelldaten, ungültige Vektoren und den neuen Inferenzpfad mit Mock-NLI.

Die vollständige Suite ist inzwischen auch mit den unveränderten Kernpins plus RAG
in einer separat aus Hash-Lock installierten Python-3.12.13-Umgebung geprüft:
241 Tests plus 6 Subtests. Abhängigkeiten und Ergebnisse stehen in `VALIDIERUNG.md`.
Der im Bestand vorgesehene Python-3.12.6-Container wurde hier nicht gebaut. Optionale RAG-Tests werden ohne Qdrant
übersprungen; bei der Entwicklung müssen die RAG-Abhängigkeiten installiert sein.

## Nächste Versuchsreihe für vier Personen

1. **A: Daten / Evaluation.** Vorlagenweise getrennte Dev-/Testdaten festlegen, freie
   Claims und Sprachpaare ergänzen. Vorhandene optimierte Daten nur als Regression.
2. **B: Retrieval.** BM25, Vektor und Fusion vergleichen; identische Chunks und gleiches
   Kontextbudget als zusätzliche kontrollierte Ablation. Embedding-Parameter nur auf Dev ändern.
3. **C: Integration.** Apertus-Prompt und Inferenzparameter konstant halten. End-to-End-
   Ergebnisse und tatsächlich verwendetes Modell protokollieren. Danach UI/MCP integrieren.
4. **D: Belege / Analyse.** Belegqualität, Sprecherverwechslungen und Fehlfälle prüfen;
   Indexaufbau, Suchzeit, Token, Latenz und Reproduzierbarkeit dokumentieren.

Zuerst Retrieval-Recall/Hit@k auf gelabelten Belegen prüfen, dann NLI-Macro-F1 und
bestehendes Evidence-Hit@5. Indexbauzeit und Modell-Ladezeit getrennt von warmen
Suchzeiten berichten. Der Smokevergleich verwendet Baseline-Seiten gegen kleinere
Vektorchunks und isoliert deshalb den Embedding-Effekt noch nicht. Auch die Zahl
`top_k` alleine garantiert kein gleiches Kontext-/Tokenbudget.

Freie Claims und Originalclaims getrennt berichten: Im vorhandenen Datensatz nennen
viele Aussagen ihre Quellsektion. Eine hilfreiche semantische Suche kann bei freien
Claims gewinnen, ohne den bestehenden Wettbewerbs-Score zu verbessern.

Erst nach diesem Vergleich über zusätzliches NLI-Modell oder Training entscheiden.
Die Nachricht ist keine Aufforderung, Apertus zwingend zu ersetzen.

## Technische Quellen

- [E5-Modellkarte](https://huggingface.co/intfloat/multilingual-e5-small): Präfixe,
  normalisierte Embeddings und Kontextgrenze.
- [Sentence Transformers](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
- [Qdrant Python Client](https://python-client.qdrant.tech/)

## Fortsetzung: gepaarte Evaluation

Der nächste vertikale Schritt ist jetzt implementiert:
[EVALUATION.md](EVALUATION.md) enthält Startbefehle, die kontrollierte Chunk-/Budget-
Ablation und die klare Trennung von Referenz-Proxys und echter NLI-Qualität.
[CLAUDE_REVIEW.md](CLAUDE_REVIEW.md) dokumentiert die tatsächliche Zusammenarbeit
und übernommene sowie zurückgestellte Reviewbefunde.
