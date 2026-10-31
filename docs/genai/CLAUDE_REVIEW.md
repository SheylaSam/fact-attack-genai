# Zusammenarbeit Codex / Claude Code am 31.10.2026

Claude Code CLI 2.1.292 war installiert und angemeldet. Zwei tatsächliche unabhängige
Reviews wurden mit `claude -p`, Lesewerkzeugen und ohne Dateiänderungen oder
NLI-Modellaufrufe ausgeführt. Codex führte Implementierung und Tests durch.
Keine Codex-Unteragenten wurden als Claude-Ersatz verwendet.

## Übernommene Befunde

- Qdrant-Client bei fehlgeschlagenem Konstruktor schliessen; Retry mit demselben
  Indexverzeichnis ist getestet. CLI und standalone Evaluator schliessen Engine
  auch bei Fehlern. Encoder wird innerhalb einer Engine über Büchlein geteilt.
- In-Memory-Vektorcache berücksichtigt jetzt Absatzinhalt statt nur Dateipfad.
  Metadaten werden innerhalb desselben Locks gelesen wie der Suchaufruf.
- Fusion nimmt nur positive lexikalische Treffer in ihre RRF-Rangliste auf.
- Bisheriger Holdout ist beobachtete Regression; veraltetes Manifest korrigiert.
- Gleiche Chunks und gleiches E5-Textbudget als kontrollierte Ranking-Ablation.
  Originalpipeline separat mit ursprünglichem und begrenztem Kontext erfassen;
  cue-basierte Teile nicht zusätzlich nach Seitenposition auf top-k abschneiden.
- Sprachparameter explizit übergeben; aktive Baseline-Konfiguration protokollieren.
- Referenz-Hit-Proxys, offizielles Quote-Längenlimit, kleine Stichprobe und
  unterschiedliche Timing-Grenzen explizit kennzeichnen. NLI-Prompt ist konstant,
  aber dieser Evaluator bildet nicht die gesamte Produktions-NLI nach.

## Bewusst nicht als Bugfix umgesetzt / offene Experimente

- Sprecher-/Legal-Caps zählen im Vektorpfad weiterhin Chunks. Das bleibt eine
  experimentelle Pipelineoption; die kontrollierte Ranking-Ablation nutzt diese
  Heuristiken in keinem der drei Chunk-Arme. top-k bedeutet dort Retrievaleinheiten,
  nicht unterschiedliche Seiten. Distinct-page-Zahlen werden zusätzlich erfasst.
- Lock serialisiert Engine-Suche bewusst für lokale Qdrant-/Encoder-Nutzung;
  Evaluation läuft sequenziell und verspricht keinen Thread-Speedup.
- Zu lange Dense-Queries schlagen explizit fehl. Keine stille Kürzung/Fallback,
  die die Vergleichbarkeit verändern würde.
- Vote-im-Query und Übersetzung der BM25-Anfrage sind spätere Dev-Ablationen.
- Unvollständige persistente Indizes werden weiterhin klar abgewiesen statt
  automatisch überschrieben. Die Dateisperre wird jetzt zuverlässig freigegeben.
- Der NLI-Aufrufdeckel zählt logische Aufrufe; interne Retry-/Repair-Anfragen
  können darüber hinausgehen. Aktuell gab es mangels API-Key null NLI-Anfragen.
- Kein vollständiger Page-Gold-Recall ohne manuelle Seitenannotation; keine
  Labels oder Belegseiten aus Referenzähnlichkeit erfinden.
