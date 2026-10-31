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

## Fortsetzung: mehrsprachige Dev-Diagnose

Zwei weitere tatsächliche read-only CLI-Reviews wurden in der Fortsetzung ausgeführt.
Claude bestätigte die 42 unverändert übernommenen OST-Fälle (27 crosslingual, neun
Sprachpaare) und fand keinen Daten-/Laufblocker. Sein lokaler Standard-Python hatte
nicht alle Projektabhängigkeiten; die Testausführung erfolgte deshalb durch Codex
in der bekannten externen `work/rag-venv`-Umgebung.

Übernommen:

- Native Baseline-Kontextreihenfolge ist keine Retrieval-Rangfolge. Neuer
  `context_reference_hit` berücksichtigt alle tatsächlich gelieferten Passagen;
  alte first-k-Felder bleiben als solche erhalten und werden klar eingeordnet.
- Ranking und Budgeteffekt getrennt speichern, volle Treffertexte optional mit
  Position, physischen Seiten, Chunk-ID, Score und Text-Hash erfassen.
- Kurze abgeschnittene Texte können den Legacy-Fuzzy-Proxy fälschlich erfüllen.
  Separater exakter Wort-5-Gramm-Abdeckungswert ergänzt die Diagnose. Keine
  Umbenennung in semantische Belegqualität.
- Gemeinsame Referenztexte als Cluster sichtbar machen, inklusive gleichgewichteter
  Cluster-Mittelwerte. Dies sind keine nachgewiesen unabhängigen Claimfamilien.
- `[]` als geprüft leere Seitenannotation akzeptieren, von `null` unterscheiden
  und jeweils aus undefiniertem Seiten-Recall ausschliessen. Ungültige Seitentypen
  und nicht vorhandene physische Seiten klar abweisen.
- Auch Fehler beim Erzeugen der Report-Metadaten müssen Engine schliessen und
  geänderte Konfigurationswerte wiederherstellen; gezielter Fehlertest ergänzt.

Nicht übernommen / bewusst begrenzt:

- Die Empfehlung, aus automatisch gefundenen Referenz-PDF-Übereinstimmungen
  unmittelbar Goldseiten zu machen, wird ohne zusätzliche manuelle Prüfung nicht
  als unabhängige Annotation übernommen. Es wurden keine Goldseiten erfunden.
- Aus nur 28 beobachteten Fällen und einem Fuzzy-Proxy wird auch bei einem grossen
  Abstand keine robuste allgemeine Modellrangfolge abgeleitet.
- Kein voller Parametersweep und kein neues Embedding-Modell. Zunächst zwei
  vorab festgelegte Chunkkonfigurationen auf denselben 42 Fällen, mit demselben
  maximalen Kontextbudget und ausreichend Kandidaten zur Budgetfüllung.

## Fortsetzung: Quellzitate, Silver-Anmerkungen und Parserfehler

Drei weitere reale read-only Reviews: (1) unabhängige Prüfung des Silver-/NLI-
Replay-Konzepts, (2) Einzelprüfung aller zwölf Quellzitate gegen die vollständigen
exportierten physischen Seitentexte, (3) Codeprüfung des neuen Validators und der
Parserkorrektur. Codex führte zusätzlich die visuelle Prüfung von elf PDF-Seiten
und alle lokalen Tests durch. Claude bestätigte zwölf Zitate, benannte drei
semantische/Attributions-Einschränkungen; diese bleiben im Sidecar erhalten und
werden in einer separaten Sensitivitätsgruppe ausgeschlossen.

Übernommene neue Codebefunde: explizite Soft-Hyphen-Zeilenumbrüche behandeln;
suspensive Bindestriche erhalten; echte grossgeschriebene Zusammensetzungen
behalten; Quick- und Vollparser angleichen; Cacheversion erhöhen; Fehler bei
fehlenden Fall-IDs verständlich melden; Büchlein-/Sprachidentität sowie die
Beziehung gekürzter Kontexte zu ihren ursprünglichen Passagen prüfen; Parser- und
Textnormalisierungshashes auch im Belegreport führen. Zitatlänge/Chunkgrösse ist
als Confound dokumentiert. Die offizielle Fuzzy-Normalisierung bleibt unverändert.

Nicht übernommen: historische Reports pauschal wegen abweichenden aktuellen
Parsers verwerfen. Für den absichtlichen Vorher-Nachher-Vergleich bleiben sie
notwendig; historische Quellhashes und aktueller Scorer werden getrennt aufgeführt.
Die Silver-Scorereports sind keine Retrievalreports und brauchen deshalb nicht
dieselben Hittext-Felder; sie referenzieren die konkreten Retrievalreporthashes.

Das vorgeschlagene strikt gedeckelte und journalisierte Online-NLI-Replay ist
weiterhin offen. Die bestehenden logischen Aufrufgrenzen werden nicht als harte
HTTP-Anfragegrenze ausgegeben. Es gab keine echte Apertus-Anfrage.

## Reales NLI mit wiedergefundenem Zugang

Die Nutzerin nannte den bisherigen Checkout unter `/Users/sheyla/Projects/…`.
Dessen `.env` enthält einen gültigen Zugang; Codex bestätigte Modellverfügbarkeit
mit einer lesenden Abfrage. Claude prüfte den neuen festen Kontext-Smoke **vor**
dem ersten NLI-Aufruf ohne Zugriff auf die `.env`. Übernommen wurden: Rohantworten
vor Scoring journalisieren, vollständiges JSON und explizite gültige Labels
verlangen, Ausgangsdateien nicht überschreiben und das Zwei-Fälle-Design erzwingen.
Der Requestzähler greift vor jeder tatsächlichen HTTP-Sendung inklusive Reconnect.

Erster echter Smoke abgeschlossen: elf Anfragen, zwölf Ergebnisse mit einer
identisch wiederverwendeten Antwort. Kein Modellwechsel oder Mock. Danach reine
Offline-Korrektur der Neutral-Evidence-Darstellung, keine wiederholte Inferenz.
Details: [APERTUS_ZUGANG_UND_SMOKE.md](APERTUS_ZUGANG_UND_SMOKE.md).

## Vollständiger 42-Fälle-NLI-Vergleich

Drei tatsächliche read-only Reviews für diesen Schritt: Anforderungen des langen
Laufs, Prüfung des neuen `run_nli42.py` vor den Anfragen sowie unabhängiger Audit
der fertigen 126 Ergebniszeilen. Keine Env-Datei wurde an Claude übergeben und
Claude führte keine kostenpflichtigen Modellaufrufe aus.

Übernommene Punkte: fortsetzen nach einzelnen Formatfehlern ohne Neutral-Fallback;
Abbruch bei systemischen HTTP-/Modellfehlern; Kampagnendatei gegen erneuten
Budgetverbrauch; rotierende Armreihenfolge; Raw-Response-Prüfung der Cachetreffer;
striktes Wahrscheinlichkeits-/Belegschema; vollständige Paarung als Primäranalyse;
sekundäre Auswertung mit Fehlern als verfehlte Vorhersagen. In diesem realen Lauf
gab es null Fehler; alle 42 Fälle blieben in sämtlichen Armen enthalten.

Die im Review noch offenen tatsächlichen Promptparameter wurden anschliessend
rein offline gegen die protokollierten HTTP-Payloadhashes geprüft: 105/105 neue
Anfragen und alle sechs verwendeten früheren Antworten stimmen bytegenau mit den
rekonstruierten aktuellen Eingaben überein. Es gab keine zusätzlichen API-Aufrufe.

Claude bestätigte die Macro-F1-Werte, die 42/37/33 korrekten Labels, Cache-/Journal-
Zählung und fehlende Label-/Referenzübergabe an NLI. Wichtige Einschränkungen stehen
im Ergebnisbericht. Zwei Reviewdetails wurden nachgeprüft und präzisiert: sechs
übersetzte Vote-Titelstrings bezeichnen **zwei** Vorlagen; nach Deduplikation bleiben
**sechs** Contradictionfälle, nicht fünf. Die 37 eindeutigen Eingaben verteilen sich
auf 22 Entailment, neun Neutral und sechs Contradiction.

Keine inferenzstatistische Signifikanz aus naivem Fall-Bootstrap/McNemar abgeleitet:
Die beobachteten Fälle teilen Claims/Referenzen und ein Büchleindatum. Keine weiteren
API-Läufe oder neue Promptoptimierung nach Sichtung dieser Ergebnisse.
