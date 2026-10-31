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
