# Sprachübergreifende Dev-Auswertung: Fortsetzung am 31.10.2026

Die neue Messung ergänzt die bisherigen 28 monolingualen Demo-Fälle um einen
separaten offiziellen Dev-Ausschnitt. **42 unverändert gelabelte OST-Zeilen**, davon
27 crosslingual und 15 monolingual, alle neun Sprachpaare. 37 unterschiedliche
Claimtexte, 24 unterschiedliche Referenztexte. Alle 42 Fälle haben erkannte
Abschnitts-Cues; neue freie Claims und unabhängige Testdaten sind nicht enthalten.
Labels: 25 Entailment, 11 Neutral, 6 Contradiction. Quelle/Hashes und Auswahlregel:
`track_2a/data/genai/ost_june_dev.manifest.json`.

Zwei vorab festgelegte Chunkkonfigurationen, gleiche Fälle, 20 maximale Kandidaten
und 1536 maximale E5-Texttokens. Die drei kontrollierten Chunk-Arme füllen in diesen
Läufen das Textbudget jeweils aus. Alle übrigen Retrieval-/NLI-Defaults bleiben
unverändert; automatische Query-/Claimübersetzung ist hier ausgeschaltet.

## Tatsächliche lexikalische Diagnose

Anteil der exakten Wort-5-Gramme der Referenz, die im gesamten gelieferten Kontext
vorkommen (Mittelwert über Fälle). **Keine semantische Belegmetrik und kein NLI-F1.**

| Chunkkonfiguration | Arm | Alle 42 | Crosslingual 27 |
|---|---|---:|---:|
| 384/64 | bm25_chunks | 23.0% | 24.0% |
| 384/64 | vector | 37.3% | 39.1% |
| 384/64 | vector_hybrid | 33.2% | 35.5% |
| 128/32 | bm25_chunks | 20.8% | 19.9% |
| 128/32 | vector | 27.0% | 28.0% |
| 128/32 | vector_hybrid | 24.4% | 24.2% |

Beim 384/64-Lauf hat Dense gegenüber BM25 auf denselben Chunks in 34 Fällen eine
höhere, in vier dieselbe und in vier eine niedrigere Wortfolgenabdeckung. Für die
27 crosslingualen Fälle lautet die Aufteilung 21/2/4. Der mittlere gepaarte Abstand
beträgt dort +15.1 Prozentpunkte; bei gleicher Gewichtung der 17 Referenztext-Cluster
+13.3 Prozentpunkte. Dies ist deskriptiv: Referenzcluster sind keine bewiesenen
unabhängigen Claim-/Paraphrasenfamilien, die Daten wurden bereits zur Entwicklung
verwendet. Keine Konfidenz-/Signifikanz- oder Generalisierungsaussage.

Die cue-basierte Baseline erreicht insgesamt 56.2 % Wortfolgenabdeckung und 57.3 %
bei den crosslingualen Fällen – mit im Mittel nur 897 bzw. 886 E5-Texttokens. Ihr
Kontext hat eine andere Auswahl und ist deshalb kein isolierter Embeddingvergleich.
Sie bleibt ein wichtiger, effizienter Referenzpunkt und wird nicht ersetzt.

Die kleineren Chunks erhöhen manche Fuzzy-Trefferzahlen, während die exakte
Wortfolgenabdeckung sinkt. Beispiel Dense: Legacy-Kontexttreffer 30/42 bei 384/64
gegen 36/42 bei 128/32; zugleich sinkt die Wortfolgenabdeckung von 37.3 % auf 27.0 %.
Das zeigt, weshalb die Fuzzy-Zahl allein keine Auswahlentscheidung rechtfertigt.
Auch die Wortfolgenabdeckung ist nur eine Diagnose: Sie gewichtet den ganzen
Referenzabschnitt und misst nicht, ob die für einen Claim nötige Belegstelle da ist.
Die Konfigurationen unterscheiden sich zudem in Chunklänge und Überlappung.

Beim 384/64-Lauf verlieren BM25, Dense und Fusion durch Kontextkürzung in 17, 10
und 13 Fällen einen vorher positiven Legacy-Kontexttreffer. Bei 128/32 sind es
1, 2 und 3. Diese Zahlen beschreiben den Proxy, nicht verlorene Goldbeweise. Volle
Texte vor/nach Kürzung, Positionen, Chunk-IDs, Seiten und Scores sind in den JSONs
für eine konkrete Fehlersuche gespeichert.

## Aufbau und Reproduzierbarkeit

504 Ergebniszeilen: 42 Fälle × sechs Arme × zwei Konfigurationen, echte lokale
E5-Embeddings, Modellrevision `614241f622f53c4eeff9890bdc4f31cfecc418b3`.
Die Indizes wurden hier tatsächlich neu aufgebaut (`index_reused=false`).
384/64: 56/59/58 Chunks für DE/FR/IT, Aufbau 3.38/3.27/2.83 Sekunden.
128/32: 144/159/152 Chunks, Aufbau 3.58/3.02/2.14 Sekunden.
Modellladen und warme Suchzeiten stehen separat in den Reports. Das sind lokale
Einzelmessungen, keine stabile Performancerangfolge. Die Zeit für nachträgliches
Scoring/Diagnosen ist nicht Bestandteil der gemessenen Suchlatenz.

Quellstand der Retrievalmessung: `f241b01`, zusätzlich Quellcodehashes, Dataset-/PDF-
Hashes, aktive Konfiguration, Budget und Modellidentität in beiden Reports.
Gepaarte Diagnose: `ost_june_chunk384_pairs.json` und `ost_june_chunk128_pairs.json`.
Startbefehle und genaue Definitionen: [EVALUATION.md](EVALUATION.md).

## Was noch fehlt

Es gab **keine echten Apertus-NLI-Aufrufe**: API-Schlüssel weiterhin nicht vorhanden.
NLI Macro-F1, echtes Evidence Hit@5, tatsächliche NLI-Tokens und Inferenzzeiten sind
nicht gemessen. Es gibt noch keine geprüften Belegseiten für Page-Recall/Hit@k.

`data/genai/evidence_review_packet.jsonl` bereitet 24 Referenzgruppen mit allen
42 Claims, Original-NLI-Labels und Seitenvorschlägen vor. Jede einzelne
Claim-Belegannotation ist `unreviewed`, `gold_evidence_pages=null`. Kandidaten
müssen im PDF claimweise geprüft werden; gemeinsame Referenzen bedeuten nicht
identische Belege. Dieses Paket ist keine neue Goldannotation und kein Testset.

Der sinnvolle nächste Qualitätsnachweis braucht geprüfte Belegseiten und danach
einen begrenzten gepaarten Lauf mit konstantem Apertus-Modell/Prompt. Die aktuelle
Messung rechtfertigt weitere Untersuchung mehrsprachiger Suche, keine Änderung
der Standardpipeline oder Behauptung eines verbesserten Wettbewerbsscores.
