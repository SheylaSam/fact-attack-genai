# Vorhandener Apertus-Zugang und erster echter NLI-Smoke

31.10.2026. Der gültige Zugang liegt bereits in
`/Users/sheyla/Projects/Apertus-Team-Fact-Attack-2026/track_2a/.env`.
Dieser bisherige Projektordner enthält lokale Änderungen und wurde nicht verändert.
Die zuvor geprüften Checkouts enthalten nur `.env.example`-Vorlagen. Deshalb
hatte deren Konfiguration korrekt keinen Schlüssel geladen. Ein neuer Account war
für diesen Test nicht erforderlich.

Die vorhandene Datei konfiguriert `LLM_API_KEY`, `LLM_BASE_URL` und das Modell
`swiss-ai/Apertus-v1.5-70B-thinking`. Eine lesende `/models`-Abfrage bestätigte
Zugang und Verfügbarkeit. Danach wurden die Zugangswerte ausschliesslich im
laufenden Python-Prozess verwendet. Keine Schlüsselwerte oder ausgefüllte `.env`
wurden kopiert, ausgegeben oder eingecheckt.

## Tatsächlicher Lauf

Zwei bereits beobachtete crosslinguale Dev-Fälle: `ost-0000` (Entailment) und
`ost-1208` (Contradiction), sechs Retrievalarme, exakt gespeicherte Kontexte aus
`silver12_retrieval_dehyphenated.json`. Keine erneute Embeddingberechnung.

- **Elf tatsächliche POST-Anfragen, elf erfolgreiche Inferenzantworten.**
- Zwölf gepaarte Ergebnisse; ein identischer Claim/Kontext wurde wiederverwendet.
- Angefordertes und vom Server gemeldetes Modell stimmen überein.
- `mock=false`; kein stiller Modellwechsel, kein automatischer Reparaturlauf.
- Vom Server gemeldete Tokens: **25 766 Prompt**, **1 881 Completion**.
  Diese Zahlen sind keine Preis-/Rechnungsprüfung und enthalten keine doppelte
  Berechnung für die wiederverwendete Antwort.

Native Baseline und Dense klassifizierten in diesem Zugangstest beide Fälle
entsprechend den ursprünglichen offiziellen Labels. Andere Arme machten Fehler.
Bei Dense lagen die Modellzitate ausserhalb des offiziellen Referenz-Hit-Proxys.
**Das ist ein Funktionstest, keine belastbare Modell-/Strategierangfolge.**
Es gibt keinen Neutralfall und nur zwei Fälle. Die im JSON gespeicherten
Macro-F1-Diagnosen sind entsprechend als `smoke_only` bezeichnet.

Der Prompt ist der konstante JSON-Prompt von `ApertusClient.infer` mit den
vorher gespeicherten Page-Kontexten, nicht die gesamte optimierte Produktions-NLI.
Rohlabel und nachgelagerte Entscheidung werden getrennt gespeichert. Neutral-
Ausgaben haben für die offizielle Evidence-Diagnose keine Belege. Die Modellzitate
bleiben zusätzlich als `raw_evidence` erhalten. Diese reine Score-Korrektur erfolgte
an gespeicherten Antworten, ohne neue Inferenz; die ursprünglichen Journalereignisse
bleiben unverändert.

## Begrenzung und Audit

`fixed_context_nli_smoke.py` reserviert vor **jedem tatsächlichen HTTP-Sendeversuch**
einen fsync-geschriebenen Journaleintrag. Auch Verbindungswiederholungen zählen.
Maximal zwölf Versuche sind zulässig. LLM-Retries, JSON-Reparatur und automatische
Modellumschaltung sind deaktiviert. Vollständiges JSON mit explizitem gültigem
Integerlabel ist erforderlich; fehlendes/kaputtes JSON wird nicht als Neutral
gewertet. Rohe Antworten werden vor weiterer Verarbeitung protokolliert.

Ein vorhandenes Journal oder Ergebnis wird abgewiesen, damit ein versehentlicher
zweiter bezahlter Lauf nicht automatisch startet. Dies ist **kein automatisches
Resume**: unklare reservierte Anfragen sind vor jeder Wiederholung zu prüfen.
Ein Fehler bricht den Smoke ab und schreibt eine unvollständige Ausgabe ohne
paarweise Qualitätsscores. Quellcode-, Dataset- und Retrievalreporthashes stehen
im Report. Das ausgeführte Script war Stand `2e6955c`; anschliessend wurde nur
Neutral-Evidence-Nachbearbeitung korrigiert und mit ihrem Hash dokumentiert.

Reproduktionsbefehl, für einen bewusst neu angelegten Ausgabepfad:

```sh
cd track_2a
PY=/Users/sheyla/Documents/ChatGPT/GenAI/work/rag-venv/bin/python
$PY scripts/fixed_context_nli_smoke.py \
  --dataset data/genai/ost_june_dev.jsonl \
  --report results/genai/silver12_retrieval_dehyphenated.json \
  --env-file /Users/sheyla/Projects/Apertus-Team-Fact-Attack-2026/track_2a/.env \
  --output results/genai/real_nli_access_smoke.json --max-requests 12
```

Der vorhandene Ausgabepfad ist bereits belegt und darf nicht blind wiederholt
werden. Für einen grösseren Vergleich braucht es eine vorab festgelegte, label-
und sprachbewusste Stichprobe, vollständige Paarung und ein explizites Anfragebudget.
Die Silver-Belegannotation ersetzt weiterhin keine menschliche Goldprüfung.
