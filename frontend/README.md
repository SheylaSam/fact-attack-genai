# Fact Attack · React-Agentenoberfläche

React, TypeScript und Vite im Browser; FastAPI und der vorhandene LangGraph-Agent im Python-Backend.
Die Oberfläche benötigt zur Laufzeit keine externen Schriftarten, CDNs oder UI-Dienste.

## Lokal starten

Aus dem Repository-Hauptordner, mit Python 3.12 und Node.js 22.12+:

```sh
make setup-web
make web
```

`make web` nutzt `track_2a/.venv/bin/python`, baut das Frontend und startet **http://127.0.0.1:8000**.
Mit Ctrl+C beenden. Ist Port 8000 belegt, zuerst den dort laufenden Server beenden.
Die bisherige Oberfläche kann über `make streamlit` auf Port 8501 gestartet werden.

Vorhandene `.env` beibehalten. Das Backend liest zuerst `track_2a/.env`, danach die Datei im Hauptordner;
`FACT_ATTACK_ENV_FILE` kann explizit eine andere Konfiguration wählen. Für Live-Prüfungen muss ein gültiger
`API_KEY` und bei Bedarf `BASE_URL` gesetzt sein. Der Schlüssel wird nie ans Frontend gegeben.
Eine geänderte `.env` erfordert einen Backend-Neustart.

## Was funktioniert

- Freie Behauptungen mit auswählbarer Quelle und Behauptungssprache DE/FR/IT.
- Live-Modus über Apertus; expliziter Demomodus mit heuristischem Mock und ohne Modellanfragen.
- Live-Schritte aus dem vorhandenen Agentengraphen, Anfragebudget von 1 bis maximal 12 und Abbruch.
- Rückfragen zur Vorlage setzen dieselbe Sitzung fort; zusammengesetzte Aussagen können einzeln neu geprüft werden.
- Validierte Zitate und PDF-Seiten, Original-PDF im Dialog, Bericht als Text, JSON und Zwischenablage.
- Quellenbibliothek mit lokaler Suche, Sprachfilter und PDF-Upload bis 20 MB. Geschützte oder beschädigte PDFs werden
  abgelehnt. Die Dokumentsprache wird heuristisch aus den ersten Seiten erkannt; die Behauptungssprache ist separat wählbar.
- Lokaler SQLite-Verlauf, Suche, Wiederöffnen, erneute Prüfung und Löschen nach Bestätigung.
- Entwurf im Browser, dunkles/helles Design, mobile Navigation und Tastaturbedienung.
- Eine Prüfung läuft bei Browser-Neuladen weiter. Unterbrochene SSE-Verbindungen werden mit Statusabfragen überbrückt;
  dadurch werden keine zusätzlichen Prüfungen gestartet.

## Quellen und automatische Suche

Mit einer gewählten Quelle wird das ganze Büchlein als feste Quelle an den **bestehenden Agenten** übergeben.
Die vorhandene Retrieval-Pipeline sucht darin Textstellen. Das funktioniert ohne Embedding-Modell und ohne Qdrant.
Für solche temporären Quellen hat die Vektornachsuche wie in der bisherigen Oberfläche keine zusätzlichen Passagen.

Automatische Vorlagensuche nutzt unverändert den vorhandenen E5-/Qdrant-Agentenindex und dessen kalibrierten Wächter.
Sie ist nur verfügbar, wenn die Zusatzpakete und ein fertig aufgebauter Index vorhanden sind. Ein fehlender Index
wird nicht durch eine ungeprüfte Ersatzsuche überspielt. Einrichtung aus dem Hauptordner:

```sh
track_2a/.venv/bin/python -m pip install -r track_2a/requirements-rag.txt
track_2a/.venv/bin/python track_2a/scripts/fetch_catalog_booklets.py --help
# Optional benötigte offizielle Büchlein gemäss dessen CLI herunterladen.
track_2a/.venv/bin/python track_2a/scripts/build_agent_index.py
```

Beim ersten Indexaufbau kann das öffentliche E5-Modell heruntergeladen werden. Für einen bereits vorbereiteten
Modellcache `HF_HOME` setzen und optional `HF_HUB_OFFLINE=1` verwenden. Automatische Suche derzeit nur über deutsche
Büchlein; bei FR/IT eine Quelle ausdrücklich wählen. Siehe auch `docs/genai/AGENT_STUFE1.md`.

## Daten, Laufzeit und Grenzen

Diese Version ist eine **lokale Anwendung für eine Person**, kein öffentlich gehosteter Dienst.
Der Server bindet an 127.0.0.1, prüft Hosts und blockiert Schreibzugriffe aus fremden Browser-Origins.
In-Memory-Agentensitzungen erfordern **einen Uvicorn-Worker**. Es laufen höchstens zwei Prüfungen parallel;
Zugriffe auf den gemeinsamen automatischen Suchindex sind serialisiert.

Live-Prüfungen senden Behauptung und relevante Textstellen an den konfigurierten Modell-Endpoint, auch bei eigenen PDFs.
Der Demomodus verwendet echte lokale Dokumente, aber simulierte Modellantworten: kein Nachweis für inhaltliche Qualität.
Ein Abbruch sperrt weitere Modellanfragen; eine bereits gesendete Anfrage lässt sich nicht zurückholen.
Nach einem Backend-Neustart sind offene Prüfungen als unterbrochen markiert und werden nicht automatisch wiederholt.

Prüfungen liegen in `track_2a/data/.local-history/agent-ui.sqlite3`, getrennt vom früheren Streamlit-Verlauf.
`FACT_ATTACK_UI_DB` kann diesen Pfad überschreiben. Uploads liegen als `custom_<sha256>.pdf` im bisherigen
Büchleinordner. Das Löschen einer Prüfung entfernt ihren Verlaufseintrag, nicht das Quelldokument.
Diese Daten werden durch `.gitignore` ausgeschlossen. Berichte enthalten Claim, Belege und Ablaufdaten;
vor einer Weitergabe auf eigene Inhalte achten.

Die neue Oberfläche konzentriert sich auf die Agentenprüfung. Die früheren Benchmark-Dashboards und Mehrfachprüfungen
sind weiterhin in der Streamlit-Oberfläche vorhanden. Der Wettbewerbspfad bleibt über seine CLI erreichbar.

## Entwicklung

Für Hot Reload zwei Terminals öffnen:

```sh
# Terminal 1, Repository-Hauptordner
make api

# Terminal 2
cd frontend
npm run dev
```

Vite läuft auf `http://127.0.0.1:5173` und leitet `/api` an Port 8000 weiter.
API-Dokumentation: `http://127.0.0.1:8000/docs`. Der normale `make web`-Start braucht nur einen Server.

```sh
cd frontend
npm test
npm run build

# Aus dem Repository-Hauptordner
cd track_2a
.venv/bin/python -m pytest tests/test_web_api.py -q
```

Die Tests lösen keine kostenpflichtigen Modellanfragen aus. API-Tests prüfen auch einen vollständigen Lauf des
vorhandenen Agentengraphen mit lokalem PDF und Mock-Modell. React-Tests prüfen Moduswahl, Absenden,
Quellendialog und die sichere Textdarstellung.

## Code

- `frontend/src/`: Browseroberfläche, Typen und API-Zugriff.
- `track_2a/src/web/api.py`: Endpoints, Worker, SQLite und SSE.
- `track_2a/src/web/runtime.py`: Streamlit-unabhängige Erstellung der vorhandenen `AgentSession`.
- `track_2a/scripts/start_web.py`: gemeinsamer Build- und Startbefehl.

Technische Grundlagen: [Vite](https://vite.dev/guide/),
[FastAPI-Dateiuploads](https://fastapi.tiangolo.com/tutorial/request-files/),
[StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/).
