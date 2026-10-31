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
