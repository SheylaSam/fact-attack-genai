# Tatsächliche Dev-Ergebnisse vom 31.10.2026

28 bestehende gelabelte Demo-Fälle: 11 DE, 8 FR, 9 IT; ausschliesslich monolingual.
25 Fälle haben erkannte Abschnitts-Cues, 3 nicht. Zwei Budgetstufen, jeweils sechs
Arme: insgesamt **336 gepaarte Retrieval-Zeilen** mit echten lokalen E5-Embeddings.
Referenz-Hits sind Fuzzy-Proxys, keine NLI-Qualitätswerte oder annotierter Page-Recall.

| Arm | Referenz-Hit@5, 1536 | Referenz-Hit@5, 768 |
|---|---:|---:|
| baseline_budgeted | 21/28 | 20/28 |
| baseline_native | 24/28 | 24/28 |
| bm25_chunks | 23/28 | 21/28 |
| bm25_pages | 22/28 | 20/28 |
| vector | 15/28 | 11/28 |
| vector_hybrid | 23/28 | 17/28 |

Die native Baseline verwendet im Mittel 3765 E5-Texttokens und ist deshalb kein
Budgetvergleich mit den übrigen Armen. Auf identischen Chunks hat reine Dense-Suche
in beiden Budgetstufen weniger Referenztreffer als BM25. Fusion erreicht bei 1536
dieselbe Trefferzahl wie BM25, bei 768 weniger. **Kein nachgewiesener Embedding-
Qualitätsgewinn; die Standardpipeline wird nicht ersetzt.** Die kleine Stichprobe
und der Proxy erlauben keine allgemeine Rangfolge der Modelle.

Bei 1536 beträgt die mittlere Referenz-Tokenabdeckung auf Chunks: BM25 0.827,
Dense 0.731, Fusion 0.837. Mittlere warme Retrieversuche: BM25 0.49 ms, Dense
17.77 ms, Fusion 15.59 ms. Diese einzelnen sequenziellen Timing-Beobachtungen sind
keine stabile Laufzeit-Rangfolge; Indexzugriffe und Betriebssystemlast variieren.
Modellladen: 3.91 s. Alle drei Büchleinindizes wurden wieder geöffnet (0.40/0.05/0.05 s),
nicht frisch aufgebaut. Neuer Indexbau wurde in dieser Messung nicht erfasst.

Die JSON-Reports enthalten getrennte Cue-/Free- und Sprachpaargruppen. Drei freie
Claims reichen für keine Aussage über freie Recherche; cross-linguale Fälle fehlen.
Alle Werte sind beobachtete Entwicklungsergebnisse, kein Testset-Nachweis.

**Nicht gemessen:** echter Page-Recall/Hit@k (keine Goldseiten), NLI Macro-F1,
Evidence Hit@5 auf Modellzitaten, tatsächliche Apertus-Tokenkosten und Inferenzzeiten.
API-Prüfung ergab keinen Schlüssel und Mock-Modus. `--nli` wurde kontrolliert gestartet
und vor Modellladen/Inferenz abgewiesen. Null Apertus-API-Aufrufe.

Quellstand der Retrievalmessung: f48d28c, zusätzlich exakte Quellcodehashes in den
Reports. Eine nachfolgende NLI-only-Korrektur schliesst Gold-Neutralfälle korrekt
vom Evidence-Score-Nenner aus; sie verändert keine der gemessenen Retrievalzeilen.

Vollständige Suite: 191 Tests plus 6 Subtests bestanden. Anschliessende Korrektur
des NLI-Evidence-Nenners: alle vier Evaluations-Tests bestanden. Bestehende fünf
PDF-Deprecation-Warnungen. Python 3.10; Python-3.12-Docker weiterhin offen.

Startbefehle und genaue Messgrenzen: [EVALUATION.md](EVALUATION.md).

Fortsetzung mit offiziellem mehrsprachigem Dev-Ausschnitt, verändertem Kandidatenlimit
und zusätzlichen Diagnosen: [ERGEBNISSE_CROSSLINGUAL_DEV.md](ERGEBNISSE_CROSSLINGUAL_DEV.md).
Die obigen historischen Demo-Zahlen sind nicht direkt mit diesem neuen Lauf vergleichbar.
