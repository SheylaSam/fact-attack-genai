# Agentenschicht, Stufe 1: Suchagent mit LangGraph, Quellenindex und CLI

31.10.2026, Branch `genai/agent-layer`. Design: [agentenschicht-design.md](../superpowers/specs/2026-10-31-agentenschicht-design.md).
Stufe 1 liefert das Grundgerüst: Eine freie deutsche Behauptung geht ohne manuelle Quellenwahl
durch einen Agenten, der die Vorlage und die Textstellen selbst sucht, prüfen lässt und die
Belege gegen das PDF kontrolliert. Es gibt eine Kommandozeile und ein lokales Ablaufprotokoll,
noch keine Oberfläche.

**Das hier ist keine Qualitätsmessung.** Die drei echten Läufe unten sind Einzelbeobachtungen
der fertigen CLI. Sie zeigen, dass die Teile zusammenarbeiten, und sie zeigen zwei Schwächen
(Rückfrage, `quelle_fehlt`). Sie sagen nichts darüber, wie oft der Agent richtig liegt. Dafür ist
das gesperrte Testset der Stufe 2 und die Evaluation der Stufe 5 vorgesehen.

## Was gebaut ist

Alles unter `track_2a/src/agent/`, getestet in `track_2a/tests/test_agent_*.py`. Der bewährte
Prüfpfad (`verify_claim`, die Wettbewerbs-CLI) bleibt B0 und ist unverändert; `inference.py`
bekam nur eine kleine Schnittstelle „prüfen mit vorgegebenen Passagen“.

| Baustein | Datei | Aufgabe |
|---|---|---|
| Quellenindex | `source_index.py` | Qdrant lokal, `intfloat/multilingual-e5-small`; 58 Vorlagen aus 20 deutschen Büchlein, 1443 Chunks. Liefert zu einer Anfrage die besten Vorlagen und die Passagen einer Vorlage. |
| Werkzeuge | `tools.py` | Sechs Werkzeuge mit validierten Argumenten (nur Vorlagen-IDs aus dem Index und Suchtext mit 1–500 Zeichen, keine Pfade, keine URLs) und der Passagenpool (höchstens 10 Passagen). |
| Entscheider | `decider.py` | Wählt die nächste Aktion: `json` (Standard), `native` (Tool-Calling des Modells) oder `scripted` (deterministisch, nur für Tests und `--mock`). |
| Graph | `graph.py` | LangGraph: Suchschleife, feste Prüfung, deterministische Belegkontrolle, Ergebnis. |
| Anfrage-Deckel | `budget.py` | Harte Obergrenze der HTTP-Anfragen pro Prüfung, mit Journal. |
| Protokoll | `trace.py` | Ein Ereignis pro Knoten und Werkzeugaufruf, im Speicher und als JSONL. |
| CLI | `__main__.py`, `cli_format.py` | Aufruf, nummerierte Schrittliste, Ergebnis, Exit-Code. |
| Einstellungen | `settings.py` | `MAX_STEPS = 5`, `MAX_REQUESTS = 12`, `MAX_PASSAGES = 10`, Timeout 180 s, höchstens 1024 Antwort-Tokens pro Entscheidung (`LLM_MAX_TOKENS`), Werkzeugmodus. |

Die Werkzeuge heißen `suche_vorlagen` (Vektorsuche über alle Vorlagen), `suche_passagen` (bewährte
Suche im ganzen Büchlein der Vorlage; Stellen zu ihrem Titel kommen zuerst, Seiten anderer Vorlagen
desselben Büchleins sind möglich), `suche_passagen_vektor` (Vektorsuche in einer Vorlage), `pruefen`,
`rueckfrage` und `quelle_fehlt`. Die sechs Ausgänge sind `gestuetzt`, `widersprochen`,
`nicht_belegt`, `rueckfrage`, `quelle_fehlt` und `technischer_fehler`. Ein technischer Fehler
(API, Budget, Schrittgrenze, Ausnahme) wird nie als `nicht_belegt` ausgegeben.

Quellenindex: Gebaut mit `track_2a/scripts/build_agent_index.py`, Bericht in
`track_2a/results/genai/agent_index_build.json` (58 Vorlagen, 20 Quellen, nichts übersprungen,
Bauzeit 96 s). Der Index selbst liegt unter `.rag-index/agent-sources/` und ist nicht im Repo.
Stand seit Stufe 2 (01.11.2026): Mit den Büchlein vom 27.09. und 29.11.2026 sind es 64 Vorlagen aus
22 Quellen und 1572 Chunks (Bericht neu gebaut); die Tabelle oben und die Zahlen davor beschreiben den Stand von Stufe 1.
Eine Eigenheit: Im Büchlein vom 25.09.2022 stehen AHV 21 und die Mehrwertsteuer-Finanzierung
in einem gemeinsamen Eintrag des Inhaltsverzeichnisses. Der Index hat deshalb 58 statt der 59
Titel des Katalogs; die Mehrwertsteuer-Vorlage hat keine eigene ID, eine Behauptung dazu landet
auf `2022-09-25_de#2`.

## Architektur

```
START → decide → act ─┬─→ decide      Suchergebnis oder abgelehnte Aktion geht zurück an den Entscheider
                      ├─→ verify → check_evidence → finish → END
                      └─→ finish      rueckfrage, quelle_fehlt, technischer Fehler
```

- **decide:** Apertus bekommt die Behauptung (als Nutzernachricht, nie im Systemprompt) und wählt
  ein Werkzeug. Im Modus `json` schreibt das Modell `{"tool": ..., "args": {...}}`, das gegen die
  Werkzeugschemas validiert wird. Eine ungültige Aktion geht als Beobachtung zurück und zählt als
  Suchschritt. Bricht die Antwort im Denkanteil ab (`<|inner_prefix|>` ohne `<|inner_suffix|>`), ist
  sie keine Aktion, auch wenn das Nachdenken JSON enthält; der Denktext geht nicht in den Verlauf.
- **act:** führt das Suchwerkzeug aus oder nimmt eine Endaktion (`pruefen`, `rueckfrage`,
  `quelle_fehlt`) nach Prüfung der Argumente an. Die Fundstellen kommen in den Passagenpool.
- **Schrittgrenze:** höchstens 5 Suchschritte; danach wird der Entscheider einmal gefragt und
  bekommt nur noch die drei Endwerkzeuge. Endet die Suche auch dann nicht, ist das ein
  technischer Fehler („Schrittgrenze“).
- **verify:** Der bewährte NLI-Pfad prüft die Behauptung gegen den Passagenpool der gewählten
  Vorlage (eine Apertus-Anfrage).
- **check_evidence:** Ein Zitat bleibt nur, wenn es normalisiert im PDF-Text seiner Seite oder in
  einem der PDF-Textblöcke dieser Seite steht. Der bewährte Pfad zitiert unter der Standard-Policy
  `raw_pages_and_blocks` ganze Seiten und Textblöcke; ein Block steht oft nicht zusammenhängend im
  Seitentext, weil Randtitel in seine Zeilen laufen. Hat ein „gestützt“ oder „widersprochen“ kein
  gültiges Zitat, wird daraus „nicht belegt“ mit dem Hinweis „Urteil ohne prüfbaren Beleg verworfen“.
  Die Seiten liest die Kontrolle wie der bewährte Pfad seine Belege (Schnellparse, gespeicherte
  Quellseiten, sonst das PDF selbst). Hat eine zitierte Seite auch dann keinen lesbaren PDF-Text, endet
  der Lauf als `technischer_fehler`, nie als „nicht belegt“. `EVIDENCE_POLICY=legacy` lehnt der Agent
  vor jedem Modellaufruf als `technischer_fehler` ab, weil die Zitate dort kein Originaltext sind.

Die Kontrolle prüft nur, **dass** ein Zitat im PDF steht, nicht, ob es zur Behauptung passt.

## Starten

Aus `track_2a/`, Python-3.12-Umgebung mit dem Lock (siehe unten). Der Quellenindex wird beim
ersten Aufruf wiederverwendet, wenn er vorhanden ist; sonst zuerst bauen:

```sh
HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python scripts/build_agent_index.py
```

**Ohne Netz, ohne Kosten (Mock):** skriptgesteuerter Entscheider und Mock-Client; keine Anfrage
verlässt den Rechner. Das Urteil des Mock ist keine Messung.

```sh
HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python -m src.agent "Die Mietrechtsvorlage stärkt die Vermieter." --mock
```

**Echt (kostenpflichtig):** braucht `FACT_ATTACK_ENV_FILE` mit dem API-Schlüssel und einen
Deckel von höchstens 12 Anfragen. Ohne die Umgebungsvariable bricht die CLI ab, bevor etwas
gesendet wird.

```sh
FACT_ATTACK_ENV_FILE=/Users/sheyla/Projects/Apertus-Team-Fact-Attack-2026/track_2a/.env \
HF_HOME=/Users/sheyla/Documents/ChatGPT/GenAI/work/model-cache HF_HUB_OFFLINE=1 \
  ../.venv312/bin/python -m src.agent "<Behauptung>" --json --max-requests 12 --trace-dir <Ordner>
```

Weitere Optionen: `--quelle VORLAGE_ID` (Vorlage fest vorgeben, keine Suche danach),
`--modus json|native|scripted`, `--index-dir`, ohne `--json` die lesbare Schrittliste.
Exit-Code 0 bei einem Ergebnis (auch `rueckfrage` und `quelle_fehlt`), 2 bei
`technischer_fehler`, 1 bei falschem Aufruf oder fehlgeschlagenem Start. Standard für
`--trace-dir` ist `.agent-traces/` (nicht im Repo); die Läufe unten liegen unter
`track_2a/results/genai/agent_stage1_traces/`. Pro Prüfung entstehen `<Lauf>.trace.jsonl`
(ein Ereignis pro Knoten) und `<Lauf>.requests.jsonl` (das Anfrage-Journal).

Die echten Läufe unten **nicht blind wiederholen**: Jeder Lauf kostet Anfragen.

## Ergebnis des Tool-Calling-Smoke

Quelle: `track_2a/results/genai/agent_tool_smoke.json` (Journal daneben). Sechs Anfragen, drei
Situationen, je einmal nativ und einmal als JSON, Temperatur 0, Modell
`swiss-ai/Apertus-v1.5-70B-thinking` am CSCS-Endpunkt. Ein Zuverlässigkeits-Smoke, keine Messung.

| Situation | Erwartetes Werkzeug | nativ | JSON |
|---|---|---|---|
| a: neue Behauptung | `suche_vorlagen` | HTTP 400 | `suche_vorlagen` (erwartet) |
| b: zwei nah beieinander liegende Mietrecht-Vorlagen | `rueckfrage` | HTTP 400 | `suche_passagen` auf der ersten Vorlage (nicht erwartet) |
| c: Passagen gefunden | `pruefen` | HTTP 400 | `pruefen` (erwartet) |

- **Nativ:** Der Endpunkt antwortet auf jede Anfrage mit `tools` und `tool_choice="auto"` mit
  HTTP 400, weil der Server ohne `--enable-auto-tool-choice` und `--tool-call-parser` läuft. Das
  ist eine Serverkonfiguration, kein Modellverhalten. Ob `tool_choice="required"` oder ein
  benanntes Werkzeug ohne diese Flags geht, wurde **nicht getestet**.
- **JSON:** 3 von 3 gültige Aktionen (angebotenes Werkzeug, gültige Argumente), 2 von 3 mit dem
  erwarteten Werkzeug. Daraus folgt `AGENT_TOOL_MODE=json` als Standard; `native` bleibt für
  einen Endpunkt wählbar, der es kann.
- **Schwäche:** In Situation b wählte das Modell trotz der Regel „Rate nie eine Vorlage“
  `suche_passagen` statt `rueckfrage` (in dieser Situation stand allerdings eine unverwandte
  Wohnungsinitiative von 2020 zwischen den beiden Mietrecht-Vorlagen). Die Stufe 3 braucht deshalb eine deterministische
  Mehrdeutigkeitsprüfung und kann sich nicht allein auf das Modell verlassen. Der erste echte
  Lauf unten zeigt dasselbe noch einmal.

## Die drei echten Läufe

Je ein Lauf pro Behauptung, nie wiederholt, `--json --max-requests 12`. Die Erwartung stand vor
dem Lauf fest und dient nur der Beobachtung. Rohdaten, vollständige Ergebnisse und Schrittlisten:
`track_2a/results/genai/agent_stage1_runs.json`; Protokolle und Journale:
`track_2a/results/genai/agent_stage1_traces/<Nr>_<Name>/`.

| # | Behauptung | Erwartung | Ergebnis | Vorlage | Anfragen | Tokens (Prompt + Antwort) | Dauer |
|---|---|---|---|---|---|---|---|
| 1 | Die Mietrechtsvorlage stärkt die Vermieter. | `rueckfrage` | **`gestuetzt`** | `2024-11-24_de#3` (Eigenbedarf) | 4 von 12 | 10 209 + 167 | 3,4 s in den Knoten, 29 s Prozess |
| 2 | Wer in den Zivildienst wechselt, muss künftig mindestens 150 Diensttage leisten. | `gestuetzt` | `gestuetzt` | `2026-06-14_de#2` (Zivildienstgesetz) | 4 von 12 | 10 425 + 218 | 3,9 s, 22 s |
| 3 | Die Schweiz führt den Euro ein. | `quelle_fehlt` | `quelle_fehlt` | keine | 1 von 12 | 1 315 + 56 | 1,0 s, 18 s |

„Dauer“: erst die Summe der Knotenzeiten (Apertus-Antworten, Suche, Prüfung), dann die
Wanduhrzeit des ganzen Prozesses, die das Laden des Embedding-Modells und das Öffnen des Index
einschließt. Alle drei Läufe endeten mit Exit-Code 0. Die Schrittlisten unten sind gekürzt
(Kosten in die erste Zeile gezogen, lange Zusammenfassungen abgeschnitten); der volle Wortlaut
der CLI steht in `agent_stage1_runs.json` unter `steps_text`.

### Lauf 1: Die Rückfrage blieb aus (nicht wie erwartet)

```
 1. ✓ suche_vorlagen  anfrage="Mietrechtsvorlage stärkt Vermieter"                (872 ms, 1321+30 Tokens)
 2. ✓ suche_passagen  vorlage_id="2024-11-24_de#3", anfrage="stärkt die Vermieter" (770 ms, 1918+46 Tokens)
      Passagen aus 2024-11-24_de#3 (bewährte Suche): 12 gefunden, 10 neu im Pool. …
 3. ✓ pruefen         vorlage_id="2024-11-24_de#3"                                  (565 ms, 2870+32 Tokens)
 4. ✓ Prüfung         vorlage_id="2024-11-24_de#3", passagen=10                     (1.2 s, 4100+59 Tokens)
      Label 0 (gestuetzt), 5 Zitate
 5. ✓ Belegprüfung    4 gültige Belege aus 5 Zitaten: gestuetzt (1 ms)
 6. ✓ Abschluss       Ergebnis: gestuetzt, 4 Belege (0 ms)
```

Der Agent hat eine Vorlage geraten. Die Behauptung sagt nicht, ob die Eigenbedarfs- oder die
Untermiete-Vorlage vom 24.11.2024 gemeint ist. Die Ergebnisliste von `suche_vorlagen` stand im
Protokoll dieses Laufs nur als Kopfzeile; **offline nachgerechnet** (derselbe Index, dieselbe Anfrage
des Agenten, kein Modellaufruf) lag Eigenbedarf mit Score 0,873 und 15 Treffern auf Rang 1 und
Untermiete mit 0,866 und 15 Treffern auf Rang 2. Der Agent suchte nur in Rang 1, prüfte und gab
„gestützt“ mit Belegen von S. 8, 41 und 42 des Eigenbedarf-Kapitels aus. Das ist der Fall, den
das Design als Misserfolg nennt (Anti-Ziel: eine Vorlage raten statt nachfragen), und derselbe
Befund wie in Situation b des Smoke, diesmal mit zwei sauber gleichauf liegenden Vorlagen. Die
Rangfolge hängt vom Suchtext ab, den das Modell formuliert: Die Kontrollanfrage des Indexbaus
(`agent_index_build.json`) mit dem Wortlaut der Behauptung reiht eine Wohnungsinitiative von
2020 zwischen die beiden Mietrecht-Vorlagen.

Ein einzelner Lauf sagt nichts über die Häufigkeit. Er genügt aber als Beleg, dass die Regel im
Prompt allein die Rückfrage nicht sichert.

„4 gültige Belege aus 5 Zitaten“: Das verworfene Zitat war echter PDF-Text, der Textblock von S. 41
„Aus all diesen Gründen empfehlen Bundesrat und Parlament, die Änderung des Obligationenrechts …
anzunehmen.“ Die damalige Belegkontrolle verglich nur mit dem Seitentext, in dem die Randtitel
„Empfehlung von Bundesrat und Parlament“ zwischen den Zeilen dieses Satzes stehen, und verwarf es zu
Unrecht. Offline mit der korrigierten Kontrolle nachgeprüft (die gespeicherten Zitate des Laufs, keine
Anfrage): 5 von 5 gültig. Der Lauf wurde nicht wiederholt.

### Lauf 2: Eindeutige Behauptung, Quelle und Beleg gefunden

Die Behauptung wurde vor dem Lauf anhand des Büchleintexts gewählt (offline gelesen:
`track_2a/data/booklets/2026-06-14_de.pdf`). S. 6 („Neu müssen alle Zivildienstpflichtigen
mindestens 150 Diensttage leisten.“) und S. 26 („Wer in den Zivildienst wechselt, muss neu
mindestens 150 Zivildiensttage leisten“) sagen es ausdrücklich. Die Behauptung ist eine leichte
Umformulierung und deshalb ein einfacher Fall, kein Test der Verallgemeinerung.

```
 1. ✓ suche_vorlagen  anfrage="Zivildienst Mindestdauer 150 Tage Wechsel"          (1.3 s, 1334+74 Tokens)
 2. ✓ suche_passagen  vorlage_id="2026-06-14_de#2", anfrage="Mindestdauer 150 Diensttage bei Wechsel …" (939 ms, 1931+58 Tokens)
 3. ✓ pruefen         vorlage_id="2026-06-14_de#2"                                  (571 ms, 2926+32 Tokens)
 4. ✓ Prüfung         vorlage_id="2026-06-14_de#2", passagen=10                     (1.1 s, 4234+54 Tokens)
      Label 0 (gestuetzt), 5 Zitate
 5. ✓ Belegprüfung    4 gültige Belege aus 5 Zitaten: gestuetzt (1 ms)
 6. ✓ Abschluss       Ergebnis: gestuetzt, 4 Belege (0 ms)
```

Die richtige Vorlage stand offline nachgerechnet klar vorn (Score 0,892, 23 Treffer; Rang 2 hat
0,855 und 5 Treffer). Die Belege liegen auf S. 26, 1, 24 und 26. Der Beleg von S. 26 enthält den
Satz mit den 150 Diensttagen. Die Belege von S. 1 (Titelseite) und S. 24 (Inhaltsübersicht der
Vorlage) sind wörtlich im PDF, tragen aber nichts zur Aussage bei: Die Belegkontrolle prüft das
Vorhandensein, nicht die Relevanz.

Das verworfene fünfte Zitat war ausgerechnet der Kernsatz, der Textblock von S. 26 „Wer in den
Zivildienst wechselt, muss neu mindestens 150 Zivildiensttage leisten …“. Wie in Lauf 1 ist er echter
PDF-Text, den die damalige Kontrolle nur gegen den durch Randtitel unterbrochenen Seitentext prüfte.
Offline mit der korrigierten Kontrolle nachgeprüft (gespeicherte Zitate, keine Anfrage): 5 von 5
gültig. Der Lauf wurde nicht wiederholt.

### Lauf 3: `quelle_fehlt` ohne Suche

```
 1. ✓ quelle_fehlt    begruendung="In den offiziellen Abstimmungsbüchlein des Bundes…   (956 ms, 1315+56 Tokens)
 2. ✓ Abschluss       Ergebnis: quelle_fehlt (0 ms)
```

Das Ergebnis entspricht der Erwartung, der Weg nicht der Absicht. Das Modell wählte `quelle_fehlt`
als allererste Aktion und rief `suche_vorlagen` nie auf, obwohl der Prompt das verlangt („Ist keine
Quelle vorgegeben, rufe zuerst suche_vorlagen auf“). Der Index wurde nicht befragt. Die Begründung
(„keine Vorlage … behandelt die Einführung des Euro“) stammt aus dem Vorwissen des Modells; der
Prompt enthält keine Liste der Vorlagen. Offline nachgerechnet hätte die Suche mit der Behauptung
als Anfrage die Bargeld-Initiative vom 08.03.2026 auf Rang 1 geliefert (Score 0,862, 13 Treffer),
einen plausibel aussehenden Kandidaten. Ein sauberes `quelle_fehlt` wäre über die Suche also nicht
sicher gewesen. Dasselbe Verhalten bei einer Behauptung, zu der es eine Vorlage gibt, würde diese
unbeachtet lassen. Stufe 3 muss `quelle_fehlt` an eine vorherige Suche und eine Score-Regel binden.

## Budgetnachweis aus den Journalen

Die Journale enthalten pro Anfrage nur Laufnummer, SHA-256 des Request-Bodys und Zeitstempel,
geschrieben und synchronisiert, bevor die Anfrage gesendet wird. Keine Bodies, kein Schlüssel.

| Lauf | Journalzeilen | Deckel | Zusammensetzung | Zeitraum (UTC, 31.10.2026) |
|---|---:|---:|---|---|
| 1 Mietrecht | 4 | 12 | 3 Entscheidungen + 1 NLI-Prüfung | 21:46:55 bis 21:46:57 |
| 2 Zivildienst | 4 | 12 | 3 Entscheidungen + 1 NLI-Prüfung | 21:47:32 bis 21:47:34 |
| 3 Euro | 1 | 12 | 1 Entscheidung | 21:48:07 |
| **Summe** | **9** | 36 genehmigt | | |

Die Zeilenzahl stimmt mit den Protokollereignissen überein, die Tokens verbuchen (Entscheidungen
und Prüfung); keiner der Läufe endete mit einem Fehler. Mit dem Smoke
(`agent_tool_smoke.journal.jsonl`, 6 Anfragen) hat Stufe 1 damit 15 Anfragen an den Endpunkt
gesendet; 3 davon (die nativen Smoke-Anfragen) lehnte der Endpunkt mit HTTP 400 ab.
Der Deckel zählt die Anfragen an den Apertus-Endpunkt auf beiden Wegen (Agent-LLM über httpx und
NLI-Client über `http.client`), Wiederholungen eingeschlossen. Anfragen an andere Dienste, etwa
LangSmith-Uploads, zählt er nicht.

## Paketversionen

Neue Pakete stehen in `track_2a/requirements-agent.txt`, `track_2a/requirements.txt` blieb
unverändert. Das Hash-Lock ist `track_2a/requirements-genai312-macos-arm64.lock.txt`
(CPython 3.12.13, macOS ARM64, 111 Pakete).

| Paket | Version im Lock | Kursversion (Design) |
|---|---|---|
| `langgraph` | 1.2.11 | 1.2.11 (gleich) |
| `langchain-openai` | 1.6.2 | 1.6.2 (gleich) |
| `langchain-core` | 1.7.3 | nicht gesondert genannt; die Version, auf die `langgraph` 1.2.11 und `langchain-openai` 1.6.2 auflösen, deshalb gepinnt |
| `openai` | 3.42.0 | indirekt (Abhängigkeit von `langchain-openai`) |
| `langsmith` | 0.14.16 | indirekt (Abhängigkeit von `langchain-core`) |
| `langchain` | nicht installiert | 1.4.0: Die Schicht nutzt `langgraph`, `langchain-core` und `langchain-openai` direkt |
| `mcp`, `langchain-mcp-adapters` | nicht installiert | 1.30.0 und 0.3.2: MCP ist Stufe 3 |

Abweichungen von den Kursversionen sind also: kein `langchain` und kein MCP in Stufe 1, dafür
das ausdrücklich gepinnte `langchain-core`. Die Kursversionen dienen laut Design nur der
Orientierung; gepinnt ist, was getestet wurde.

Der Kopf des Locks verweist auf eine Constraints-Datei `/tmp/fact-attack-genai312-constraints.txt`.
Sie existiert nur auf dem Rechner, auf dem das Lock erzeugt wurde. Der Verweis steht nur in
Kommentarzeilen; zum Installieren (`uv pip install --require-hashes -r …`) wird die Datei nicht
gebraucht. Das Lock neu erzeugen lässt sich dagegen nur mit einer entsprechenden Constraints-Datei.

## LangSmith zuschalten

Ohne Konfiguration bleibt es beim lokalen JSONL-Protokoll. LangGraph sendet seine Läufe an
LangSmith, sobald diese Umgebungsvariablen gesetzt sind, ohne Codeänderung:

```sh
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=<Schlüssel>
export LANGSMITH_PROJECT=fact-attack-agent
```

**Datenschutz:** Dann gehen die Behauptung, die Werkzeugargumente, die gefundenen Passagen und
die Modellantworten an die Server von LangSmith. Das ist im Standardbetrieb nicht der Fall und
sollte nicht ohne Absicht passieren (später zum Beispiel bei privat hochgeladenen PDFs). Wir haben
LangSmith in Stufe 1 weder eingeschaltet noch ausprobiert (kein Konto); dass die drei Variablen
genügen, folgt aus der Dokumentation von LangChain und ist hier ungeprüft.

## Grenzen von Stufe 1

- **Kein MCP.** Die Werkzeuge sind Python-Funktionen; der MCP-Server und die Anbindung folgen in Stufe 3.
- **Keine Fortsetzung der Rückfrage.** Der Lauf endet mit `rueckfrage` und den Optionen; es gibt
  keinen Checkpointer und kein `interrupt`, also keine Antwort auf die Rückfrage.
- **Keine Nachsuche.** Lautet die Prüfung „nicht belegt“, sucht der Agent nicht noch einmal mit
  anderen Mitteln. `suche_passagen_vektor` existiert, wurde aber in keinem der drei Läufe gewählt.
- **Keine Oberfläche.** Die Schrittliste der CLI ist das einzige Fenster auf den Lauf. Die Schrittzeile
  von `suche_vorlagen` zeigt die drei besten Kandidaten mit Score und Treffern; das Protokoll hält im
  Feld `data` fest, was das Modell sah (alle Kandidaten, Passagen mit Seite und Poolstatus). Die
  Protokolle der drei echten Läufe stammen von davor und haben dieses Feld nicht.
- **Keine Qualitätsmessung.** Drei Einzelläufe; Quelle der Erwartung ist unsere Einschätzung, kein
  gesperrtes Gold. Kein Vergleich mit B0.
- **Rückfrage und `quelle_fehlt` sind nicht verlässlich** (Lauf 1, Lauf 3, Smoke Situation b). Eine
  deterministische Mehrdeutigkeits- und Quellenprüfung steht für Stufe 3 an.
- **Natives Tool-Calling** ist an diesem Endpunkt nicht verfügbar; `required`/benanntes Werkzeug
  wurde nicht getestet.
- Nur Deutsch (`LANGUAGES = ("de",)`); bei vorgegebener Quelle funktioniert jede Sprache.
- Belege sind ganze PDF-Seiten oder Textblöcke des bewährten Pfads. Die Kontrolle prüft das
  Vorhandensein im PDF, nicht die Relevanz (Lauf 2: Titelseite und Inhaltsübersicht als Belege).
- Kein Wanduhr-Limit pro Prüfung; ein Modellaufruf ist auf 180 s Lesezeit begrenzt.

## Warum und wo der Agent RAG braucht (SW7, Folie 44)

Die Vektorsuche ersetzt die bewährte Suche innerhalb eines bekannten Büchleins nicht, weil der
Ersatz gemessen schlechter war (Macro-F1 1.00 gegen 0.85 und 0.72 auf den 42 Dev-Fällen, siehe
[ERGEBNISSE_NLI42.md](ERGEBNISSE_NLI42.md)). Einschränkung: Das sind Dev-/Regressionswerte, gemessen
mit dem JSON-Prompt auf eingefrorenen Kontexten, und alle 42 Behauptungen nennen ihren Abschnitt
(erkannte Abschnitts-Cues). Freie Behauptungen, wie sie der Agent bekommt, waren nicht dabei; für sie
ist der Abstand nicht gemessen. Die Vektorsuche kommt dorthin, wo die bewährte Suche blind ist:
bei der Quellenfindung über alle Vorlagen aller Termine, die heute ein Mensch per Dropdown
erledigt (gebaut als `suche_vorlagen`), und später bei der Nachsuche nach „nicht belegt“ (Stufe 3).
Ob die Vektorsuche den Gesamtwert verbessert, ist nicht gezeigt und wird erst in Stufe 5 gemessen.
