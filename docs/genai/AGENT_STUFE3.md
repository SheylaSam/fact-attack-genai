# Agentenschicht, Stufe 3: Wächter, Nachsuche, MCP-Server und gedeckelte echte Läufe

13.11.2026, Branch `genai/agent-stufe3`. Aufbau auf [AGENT_STUFE1.md](AGENT_STUFE1.md) (Suchagent) und dem
gesperrten Testset ([TESTSET.md](TESTSET.md), Abschnitt „Gesperrt“).
Stufe 3 macht den Agenten vorsichtiger und belastbarer: Ein deterministischer Wächter fragt bei mehrdeutigen
Behauptungen zurück, nach einem „nicht belegt“ gibt es genau einen weiteren Suchschritt, die drei Suchwerkzeuge
laufen wahlweise über einen eigenen MCP-Server, und die Prüfanfragen sind pro Lauf hart gedeckelt. Noch keine
Oberfläche (Stufe 4), keine Evaluation (Stufe 5).

**Das hier ist keine Qualitätsmessung.** Die fünf echten Läufe unten sind Einzelbeobachtungen. Drei davon
liefen nicht wie erwartet; das ist ein Befund über Aufbau und Schwellen, keine Fehlerquote. Einzelläufe sind
keine Messung: Dafür sind das gesperrte Testset und die Evaluation der Stufe 5 vorgesehen.

## Was gebaut ist

Alles unter `track_2a/src/agent/`, getestet in `track_2a/tests/test_agent_*.py`. Der bewährte Prüfpfad
(`verify_claim`, die Wettbewerbs-CLI) bleibt B0 und ist unverändert; `src/config.py` bekam nur einen
Schalter (`FACT_ATTACK_SKIP_DOTENV`, siehe MCP).

| Baustein | Datei | Neu in Stufe 3 |
|---|---|---|
| Wächter | `guard.py` | Bewertet die Vorlagenkandidaten einer Behauptung: `eindeutig`, `mehrdeutig`, `keine`. |
| Graph | `graph.py` | Knoten `ask` (einziger Ausgang `rueckfrage`) und `nachsuche`, Zähler `checks`. |
| Werkzeuge | `tools.py` | Hinweis nach `suche_vorlagen`, Regel für `quelle_fehlt`, Passagenpool nur aus den Seiten der Vorlage. |
| Suche | `search_backend.py` | `SearchBackend` mit `LocalBackend`; die Werkzeugfunktionen sind einmal implementiert. |
| MCP-Server und -Client | `mcp_server.py`, `mcp_client.py` | Eigener Server (stdio) mit den drei Suchwerkzeugen, synchrone Fassade `McpBackend`. |
| Anfrage-Deckel | `budget.py` | Hart pro Lauf, Journal mit fsync (aus Stufe 1, jetzt auch für die zweite Prüfung). |
| CLI | `__main__.py`, `cli_format.py` | `--quelle`, `--werkzeuge lokal\|mcp`, `checks` in `--json`, Antwortbefehle. |
| Einstellungen | `settings.py` | Schwellen, `MAX_CHECKS`, `MAX_RESEARCH_STEPS`, MCP-Timeouts. |

## Graph

```
START → decide → act ─┬─→ decide                    Suchergebnis oder abgelehnte Aktion
                      ├─→ ask ─→ finish             Rückfrage (asked_by: guard oder model)
                      ├─→ verify → check_evidence ─┬─→ finish → END
                      │                            └─→ nachsuche → decide → act → verify → check_evidence → finish
                      └─→ finish                    quelle_fehlt, technischer Fehler
```

Der Wächter sitzt in `act`. `ask` ist der einzige Knoten, der `rueckfrage` setzt. `nachsuche` kommt nur nach
einem ersten „nicht belegt“ (auch nach einem verworfenen Urteil) und erlaubt einen einzigen Suchschritt.

## Wächter

**Regel.** Der Wächter schaut auf die Rangliste von `suche_vorlagen` für den Wortlaut der Behauptung. Ein
Kandidat zählt als nah, wenn er weniger als `GUARD_SCORE_MARGIN` hinter Rang 1 liegt und mindestens
`GUARD_MIN_HITS_RATIO` mal dessen Treffer hat (höchstens vier Ränge). Zwei oder mehr nahe Vorlagen heißen
`mehrdeutig`, eine `eindeutig`. `keine` gilt ohne jeden Kandidaten oder unter `GUARD_NONE_BELOW`.

- Ohne fest vorgegebene Vorlage prüft der Wächter vor jeder Aktion mit Vorlage (`suche_passagen`,
  `suche_passagen_vektor`, `pruefen`). Ist die Behauptung `mehrdeutig`, läuft die Aktion nicht; der Graph geht
  nach `ask` mit den nahen Vorlagen (`asked_by == "guard"`).
- Ist sie `eindeutig`, aber die Aktion nennt eine Vorlage, die weder die beste noch eine nahe ist, läuft die
  Aktion ebenfalls nicht; `ask` bekommt [beste Vorlage, genannte Vorlage].
- Auch eine `rueckfrage` des Modells prüft er (ohne fest vorgegebene Vorlage, mit gültigen Optionen und solange
  Suchschritte übrig sind). Ist die Behauptung `mehrdeutig`, geht die Frage durch (`asked_by == "model"`). Ist sie
  `eindeutig` oder `keine`, lehnt der Wächter die Frage **einmal** ab und nennt die Vorlage bzw. `quelle_fehlt`;
  fragt das Modell danach erneut, wird die Frage gestellt. Die Ablehnung steht als `act`-Schritt mit Wächterdaten
  in der Spur.
- Der Hinweis am Ende von `suche_vorlagen` zeigt die Einschätzung zur **Behauptung** („Einschätzung zur
  Behauptung: …“), nicht die der Modellanfrage. Die Liste darüber bleibt die Suche des Modells. Die Suche mit der
  Behauptung läuft dafür schon hier und wird für den Wächter wiederverwendet (einmal pro Lauf).
- Mit `--quelle` bleibt der Wächter draußen: Die Person hat die Vorlage gewählt.
- Die Frage nennt Titel und Datum der Optionen.

**Warum die Behauptung und nicht die Anfrage.** Die Anfrage schreibt das Modell. Es kann sie so zuspitzen, dass
nur eine Vorlage passt, und umgeht damit die Prüfung. Der Wortlaut der Behauptung ist fest; der Wächter sucht
einmal damit und merkt sich das Ergebnis.

**Schwellen** (`settings.py`, kalibriert mit `track_2a/scripts/calibrate_guard.py`, Bericht
`track_2a/results/genai/agent_guard_calibration.json`, nur auf dem Dev-Teil des gesperrten Testsets, 37 Fälle,
fix bis zum Konfigurationsfreeze der Stufe 5):

| Schwelle | Wert | vorläufig (vor der Kalibrierung) |
|---|---|---|
| `GUARD_SCORE_MARGIN` | 0.01 | 0.02 |
| `GUARD_MIN_HITS_RATIO` | 0.0 | 0.5 |
| `GUARD_NONE_BELOW` | `None` | `None` |

Auf Dev: verpasste Mehrdeutigkeiten 1, falsche Sicherheit 0, unnötige Rückfragen 5 (vorläufig: 1 / 1 / 6). Die
Auswahl nimmt den Gitterpunkt mit dem kleinsten Tupel (verpasst, falsche Sicherheit, unnötig, Abstand,
Verhältnis); eine verpasste Mehrdeutigkeit ist der schlimmste Fehler.
**Die Belege sind dünn:**
- Der Schritt 0.005 → 0.01 beruht auf einem einzigen mehrdeutigen Fall (`sdev-01`, Abstand 0.007).
- Das Trefferkriterium ist auf Dev inaktiv (0.0 und 0.25 geben dieselben Zahlen, bei Gleichstand gilt der kleinere
  Wert). Ein Kandidat mit einem Treffer innerhalb des Abstands zählt deshalb als nah.
- „Verpasst 0“ ist auf dem Gitter unerreichbar: In `sdev-02` liegt die zweite Vorlage nicht unter den besten
  fünf. Das Gitter wurde nicht erweitert, um ein Ergebnis zu erzwingen.
- `GUARD_NONE_BELOW` bleibt `None`: Dev hat genau einen Fall ohne Abstimmung (`sdev-05`, Top-Score 0.885). Aus
  einem Fall lässt sich keine Schwelle ableiten.

## Rückfrage beantworten

Die CLI druckt zu jeder Option einen Befehl mit `--quelle <Vorlagen-ID>` (und den wiederholten Optionen wie
`--modus`, `--werkzeuge`, `--max-requests`). Die Antwort ist eine **neue Prüfung** mit eigenem Anfrage-Deckel
von höchstens 12; der alte Lauf bleibt, wie er war. Mit `--quelle` entfällt die Vorlagensuche und der Wächter.
Ausblick Stufe 4: Dort ersetzt `interrupt` das Ende bei `ask`, und der Lauf wird mit der gewählten Vorlage
fortgesetzt, statt neu zu beginnen.

## Regel für `quelle_fehlt`

`quelle_fehlt` nimmt der Graph erst nach einer Suche an: ohne feste Vorlage nach einem `suche_vorlagen` ohne
Fehler, mit fester Vorlage nach einer Passagensuche. Vorher wird es wie jede ungültige Aktion abgelehnt (die
Ablehnung ist eine Beobachtung und kostet einen Suchschritt). Ausnahme: Eine nicht deutsche Behauptung ohne feste
Vorlage endet vor dem ersten Modellaufruf als `quelle_fehlt`; das ist deterministisch und kein Modellraten.

## Nachsuche und Budget

Nach einem ersten `nicht_belegt` bekommt der Lauf **einen** weiteren Suchschritt (`suche_passagen_vektor` in
der geprüften Vorlage, mit einer vom Modell neu formulierten Anfrage) und dann die zweite, endgültige Prüfung.
Die neuen Passagen kommen zuerst in die zweite Prüfung, auch wenn der Pool voll ist. Gibt es keine neuen
Passagen, endet der Lauf mit dem ersten Ergebnis und einem Hinweis, ohne zweite Prüfung.

| Grenze | Wert | Einstellung | Was passiert an der Grenze |
|---|---|---|---|
| Suchschritte | 5 | `MAX_STEPS` | Ungültige Schritte zählen mit. Danach bekommt der Entscheider nur die drei Endwerkzeuge; endet es auch dann nicht, ist es ein `technischer_fehler` („Schrittgrenze“). |
| Suchschritt der Nachsuche | 1 | `MAX_RESEARCH_STEPS` | Zählt nicht zu den fünf, die Nachsuche bleibt also auch nach ausgeschöpften Schritten möglich. Alles außer der erlaubten Suche ist ein `technischer_fehler`, nie `nicht_belegt`. |
| Prüfungen (Belegprüfungen) | 2 | `MAX_CHECKS` | Die erste und die nach der Nachsuche; die zweite ist endgültig, es gibt keine dritte. `checks` steht in `--json`. |
| HTTP-Anfragen | 12 | `MAX_REQUESTS`, `--max-requests` | Hart pro Lauf (beide Prüfungen und alle Entscheidungen zusammen): Wiederholungen, Übersetzung und Kaskade zählen mit, jede Anfrage steht vor dem Senden im Journal. Die 13. wird nicht gesendet; der Lauf endet als `technischer_fehler`, nie als `nicht_belegt`. |

Der Passagenpool speichert höchstens 20 Passagen pro Vorlage (`POOL_LIMIT`; nur die Passagen der Nachsuche dürfen
darüber hinaus), die erste Prüfung bekommt 10.

**Behauptung statt Anfrage bei der bewährten Suche.** `suche_passagen` läuft mit dem Wortlaut der Behauptung; den
setzt der Graph, nicht das Modell (das Werkzeug hat kein Argument dafür). Gleiche Begründung wie beim Wächter:
Die bewährte Suche soll das finden, was die Behauptung sagt, nicht was das Modell daraus macht. Das Modell steuert
nur die Vektorsuche mit eigener Anfrage. In den Pool kommen nur Passagen von Seiten der gewählten Vorlage; Treffer
auf Seiten anderer Vorlagen desselben Büchleins werden nicht aufgenommen.

## MCP-Server

**Aufbau.** `mcp_server.py` ist ein FastMCP-Server über stdio mit genau den drei Suchwerkzeugen
(`suche_vorlagen`, `suche_passagen`, `suche_passagen_vektor`) und der Ressource `vorlagen://katalog`. `pruefen`,
`rueckfrage` und `quelle_fehlt` bleiben im Graphen. Die Schemas tragen dieselben Grenzen wie die lokalen Werkzeuge
(ein Test vergleicht sie). Der Client `McpBackend` (`mcp_client.py`) ist eine **synchrone Fassade**: Er startet
seine eigene asyncio-Schleife in einem Hintergrund-Thread, lädt die Werkzeuge mit `langchain-mcp-adapters` und ruft
sie von dort aus mit Zeitlimit auf. Der Graph bleibt synchron.

**Start.** `python -m src.agent "<Behauptung>" --werkzeuge mcp` startet den Server als Kindprozess
(`python -m src.agent.mcp_server --index-dir DIR`, aus `track_2a/`) und beendet ihn beim Schließen. Der Server
sendet nie eine Apertus-Anfrage: Seine Engine hat einen Mock-Client, und er bekommt nur eine Positivliste harmloser
Variablen (Prozessbasis, Modell- und Indexpfade, die nicht geheimen Einstellungen von `src/config.py`), dazu
`MOCK_APERTUS=1` und `FACT_ATTACK_SKIP_DOTENV=1`. Mit dem Schalter lädt `config.py` im Kindprozess gar keine
`.env`-Datei; Schlüssel, `BASE_URL` und `LANGSMITH_*` kommen nicht hin. Ein Test fällt, wenn `config.py` einen
Namen liest, der weder weitergereicht noch ausdrücklich gesperrt ist. Der Anfrage-Deckel zählt nur Anfragen des
Agentenprozesses; stdout gehört dem Protokoll (Bibliotheksausgaben beim Aufbau gehen nach stderr).

**Fehlerverhalten.** Ein totes, hängendes oder fehlerhaftes Server-Kind ist immer ein sichtbarer Fehler, nie ein
Ergebnis: Jeder Aufruf endet innerhalb von `MCP_CALL_TIMEOUT_S` (120 s) mit einer deutschen Fehlermeldung, der
Start innerhalb von `MCP_START_TIMEOUT_S` (120 s, Modell und Index laden). Der Graph beendet den Lauf als
`technischer_fehler` (CLI: Start fehlgeschlagen = Exit 2). Es gibt **keinen Rückfall** auf die lokalen Werkzeuge.

**Grenze.** Über MCP gibt es keine sprachübergreifende bewährte Suche: Sie bräuchte eine Übersetzung durch
Apertus, und der Server sendet keine Anfragen. Eine solche Suche wird mit einem Fehlerergebnis abgelehnt; mit
`--werkzeuge lokal` geht sie. Heute ist `LANGUAGES = ("de",)`, die Grenze wird erst mit anderen Sprachen sichtbar.

**Warum ein eigener Server.** Notiz zur Suche: Der Plan (`docs/superpowers/plans/2026-11-01-agentenschicht-stufe3.md`,
Task 5) hält **keine datierte Suchnotiz** fest, sondern nur die Annahme „für Schweizer Abstimmungsbüchlein ist
kein passender Server bekannt“. Eine Recherche mit Datum und Ergebnis ist in dieser Stufe nicht dokumentiert und wird
hier nicht erfunden; sie sollte vor einer Aussage nach außen nachgeholt werden. Begründet ist die Entscheidung so
(aus dem Modul-Docstring von `mcp_server.py`): `mcp-server-qdrant` hat ein eigenes Embedding- und Payload-Schema,
kennt weder die bewährte Suche (Stichwörter und Abschnittslogik des Wettbewerbspfads) noch die Bündelung der
Seiten pro Vorlage. Er würde die Suche auf reine Vektorsuche über rohe Chunks verengen.

**Stufe 4.** Streamlit läuft synchron und lädt Skripte neu. `McpBackend` ist dafür gedacht: Es lässt sich einmal
pro Sitzung (zum Beispiel mit `st.cache_resource`) erzeugen und aus dem synchronen Skript benutzen, ohne dass die
Oberfläche eine eigene Ereignisschleife braucht. Zu klären bleibt, wer `close()` aufruft (Prozessende), und dass
die Schleife beim Schließen geschlossen wird (seit den Korrekturen des Gesamtreviews der Fall).

## Pakete

`track_2a/requirements-agent.txt` (nicht im Wettbewerbspfad, der `requirements.txt` bleibt unverändert), Hash-Lock
`track_2a/requirements-genai312-macos-arm64.lock.txt`:

| Paket | Version | Anmerkung |
|---|---|---|
| `langgraph` | 1.2.11 | seit Stufe 1 |
| `langchain-core` | 1.7.3 | seit Stufe 1 |
| `langchain-openai` | 1.6.2 | seit Stufe 1 |
| `mcp` | 1.30.0 | Kursversion |
| `langchain-mcp-adapters` | 0.3.2 | Kursversion |

- **Abweichungen von den Kursversionen:** keine. Beide Kursversionen lösten auf.
- **Gelockerte Pins:** keine. Der Lock bekam neun neue Pins: `mcp` 1.30.0 und `langchain-mcp-adapters` 0.3.2 selbst sowie
  `cffi` 2.1.1, `cryptography` 50.0.2, `httpx-sse` 0.4.3, `pycparser` 3.12, `pydantic-settings` 2.16.2, `pyjwt`
  2.15.1 und `sse-starlette` 3.6.0. Alle bestehenden Pins blieben (`starlette`, `uvicorn` usw. waren schon da).
- Für `additionalProperties: false` im Werkzeugschema nutzt `mcp_server.py` eine private FastMCP-Schnittstelle
  (`_tool_manager`, `fn_metadata`). Das ist bewusst: `mcp` ist gepinnt, eine Umbenennung scheitert laut beim Aufbau
  des Servers, und ein Test über stdio fängt unbekannte Argumente.

## Ergebnis des nativen Smoke und `max_completion_tokens`

Quelle: `track_2a/results/genai/agent_native_smoke.json` (Journal daneben), 04.11.2026, vier Anfragen von
höchstens vier, ein Lauf, Temperatur 0, Modell `swiss-ai/Apertus-v1.5-70B-thinking`. Ein Zuverlässigkeits-Smoke.

- **Nativ:** `tool_choice="required"` wurde in 3 von 3 Situationen mit HTTP 400 abgelehnt („requires
  --tool-call-parser to be set“). Die Regel („nativ nur, wenn 3 von 3 angenommen, gültig und erwartet“) lässt
  `json` Standard (`recommended_tool_mode: json`). Wie in Stufe 1 ist das eine Serverkonfiguration, kein
  Modellverhalten.
- **`max_completion_tokens`:** Die JSON-Anfrage mit Deckel 1024 bekam HTTP 200 und blieb darunter (30
  Antwort-Tokens); der Deckel gilt als angenommen. Er steht als `LLM_MAX_TOKENS = 1024` in den Einstellungen.
  Ein abgeschnittener Denkanteil ist keine Aktion.

## Die fünf echten Läufe

13.11.2026, Freigabe durch Sheyla im Chat („Ja, starten“): fünf Läufe mit je höchstens 12 Anfragen, zusammen
höchstens 60. Jeder Lauf genau einmal, nie wiederholt, `--json --max-requests 12 --trace-dir ...`. Die Erwartung
stand vor dem Lauf in `track_2a/results/genai/agent_stage3_runs.json` (jetzt mit Ergebnis daneben); Ergebnisse,
Protokolle und Journale unter `track_2a/results/genai/agent_stage3_traces/<Nr>_<Name>/` (`result.json`,
`stderr.txt`, `*.trace.jsonl`, `*.requests.jsonl`). Alle Läufe endeten mit Exit-Code 0.

| # | Behauptung | Erwartung | Ergebnis | `asked_by` | `checks` | Anfragen (Journalzeilen) |
|---|---|---|---|---|---|---|
| 1 | Die Mietrechtsvorlage stärkt die Vermieter. | `rueckfrage` vom Wächter, ohne Prüfung | `rueckfrage`, **vom Modell** | model | 0 | 2 |
| 2 | dasselbe mit `--quelle 2024-11-24_de#2` | ein Urteil zur Untermiete | `gestuetzt` (nach Nachsuche) | – | 2 | 5 |
| 3 | Die Schweiz führt den Euro ein. | `quelle_fehlt` nach `suche_vorlagen` | `rueckfrage`, 4 Optionen | model | 0 | 4 |
| 4 | Zivildienst, 150 Diensttage, `--werkzeuge mcp` | `gestuetzt`, MCP mit echtem Apertus | `gestuetzt` | – | 1 | 4 |
| 5 | Zivildienstgesetz, rund 20 Millionen Franken Einsparung | `nicht_belegt` nach Nachsuche, `checks == 2` | `rueckfrage`, 2 Optionen | model | 0 | 2 |

**Summe: 17 Anfragen von höchstens 60** (2 + 5 + 4 + 4 + 2, je Lauf weit unter 12). Zwei von fünf Läufen (2 und 4)
verhielten sich wie erwartet.

Lauf 5: Behauptung, Seiten und Begründung wurden vorher offline notiert (`2026-06-14_de#2`, S. 6–7 und 24–39: Die
Seiten nennen keine Kosten oder Einsparungen in Franken, die einzige Zahl zu den Folgen sind die 40 Prozent weniger
Zulassungen, mit denen laut Referendumskomitee der Bundesrat rechnet, S. 30; die Behauptung ist weder belegt noch
widerlegt). Die Behauptung ist kein Fall aus `cases.jsonl`. Die Notiz vor dem Lauf, kein Fall erwähne den
Zivildienst, war falsch: Die Dev-Fälle `dev-029` und `dev-030` gehören zur selben Vorlage, `dev-030` (zusätzliche
Verwaltungskosten von rund 3 Millionen Franken, erwartet `nicht_belegt`) ist fast ein Zwilling von Lauf 5. Das
Testset war beim Lauf gesperrt und Dev ist frei; der Testteil ist nicht berührt.

Die Schrittlisten sind gekürzt (Knoten `decide`/`act`/`verify`/`check_evidence`, Argumente abgeschnitten):

```
Lauf 1  1. suche_vorlagen  "Mietrechtsvorlage stärkt Vermieter"        Hinweis: mehrdeutig (#3 Eigenbedarf, #2 Untermiete)
        2. rueckfrage      "Welche Vorlage ist gemeint? …" (Modell)    Ergebnis: rueckfrage, 2 Optionen

Lauf 2  1. suche_passagen  vorlage_id=2024-11-24_de#2                  12 gefunden, 8 neu im Pool
        2. pruefen         8 Passagen                                  Label 1 (nicht_belegt), 0 Zitate
           nachsuche       ein weiterer Suchschritt
        3. suche_passagen_vektor  "Vermieterrechte, Vorteile für …"    8 gefunden, 7 neu (Pool 15)
           Prüfung         10 Passagen                                 Label 0 (gestuetzt), 5 von 5 Zitaten gültig

Lauf 3  1. (ungültig)      keine Werkzeugwahl, kein JSON
        2. quelle_fehlt    abgelehnt: erst nach einer Suche
        3. suche_vorlagen  "Einführung des Euro in der Schweiz"        Hinweis: mehrdeutig, 4 nahe Vorlagen
        4. rueckfrage      4 Optionen (Modell)                         Ergebnis: rueckfrage

Lauf 4  1. suche_vorlagen  "Zivildienst Mindestdauer 150 Tage Wechsel"
        2. suche_passagen  2026-06-14_de#2 (über MCP)                  12 gefunden, 9 neu
        3. pruefen         9 Passagen                                  Label 0 (gestuetzt), 5 von 5 Zitaten gültig

Lauf 5  1. suche_vorlagen  "Zivildienstgesetz Änderung, 20 Millionen …"  Hinweis: mehrdeutig (Service-citoyen 0.889
                                                                       gegen Zivildienstgesetz 0.885)
        2. rueckfrage      2 Optionen (Modell)                         Ergebnis: rueckfrage
```

Budgetnachweis aus den Journalen (`*.requests.jsonl`, eine Zeile pro Anfrage, mit Zeitstempel und SHA-256 des
Körpers): 2, 5, 4, 4, 2 Zeilen, zusammen 17. Lauf 2 hat fünf Anfragen: drei Entscheidungen und zwei Prüfungen.

## Befunde der echten Läufe

Einzelläufe, keine Messung. Sie zeigen, **wo der Aufbau etwas anderes tut als geplant**.

1. **Das Modell fragt selbst zurück, bevor der Wächter eingreift.** Nach `suche_vorlagen` steht der Hinweis
   „mehrdeutig“ in der Beobachtung, und das Modell wählte sofort `rueckfrage` (Läufe 1, 3, 5, `asked_by ==
   "model"`). Der Wächter prüft nur Aktionen mit Vorlage; eine Modell-`rueckfrage` prüft er nicht. Der Hinweis
   wird außerdem aus der Anfrage des Modells berechnet, nicht aus der Behauptung. Das ist der beim Gesamtreview
   bewusst zurückgestellte Punkt (Minor 1). Folge: Lauf 1 erfüllt die Erwartung „vom Wächter“ nicht, obwohl die
   Rückfrage inhaltlich richtig ist. *Seither geändert* (Branch `genai/agent-waechter-rueckfrage`, nur offline
   getestet, kein echter Lauf wiederholt): Der Hinweis zeigt die Einschätzung der Behauptung, und der Wächter prüft
   die Modell-Rückfrage (siehe Wächter). Ob das Modell mit dem neuen Hinweis anders antwortet, zeigt erst ein
   neuer echter Lauf.
2. **Lauf 3: keine „keine“-Schwelle.** Mit `GUARD_NONE_BELOW = None` und Trefferverhältnis 0.0 zählen Kandidaten mit
   sehr wenigen Treffern (in Lauf 3: 1, 4 und 4 gegenüber 16 für Rang 1) innerhalb von 0.01 als nah. Eine Behauptung ohne Vorlage („Euro“) wird so als `mehrdeutig` mit vier
   Vorlagen gezeigt, statt zu `quelle_fehlt` zu führen. Erwartet: `quelle_fehlt`; bekommen: eine unnötige Frage.
3. **Lauf 5: Die Behauptung nennt das Zivildienstgesetz, trotzdem liegt Service-citoyen vorn** (Abstand 0.004
   zugunsten von Service-citoyen). Die Rangliste folgt Embedding-Scores und nicht dem ausdrücklichen Namen. Die Frage
   war unnötig; mit den vorläufigen Schwellen (0.02 / 0.5) wäre es ebenfalls `mehrdeutig` gewesen. Die geplante
   Nachsuche-Vorführung (`nicht_belegt`, `checks == 2`) kam in diesem Lauf nicht zustande; den Nachsuche-Pfad
   zeigt Lauf 2 (`nicht_belegt` → Nachsuche → `gestuetzt`, `checks == 2`).
4. **Was funktioniert hat.** Lauf 3 zeigt die `quelle_fehlt`-Regel (Ablehnung vor jeder Suche) und eine ungültige
   Aktion, die als Schritt zählt. Lauf 2 zeigt Nachsuche und zweite Prüfung mit gültigen Belegen. Lauf 4 bestätigt
   MCP mit echtem Apertus: Suche über den Server, Prüfung im Agentenprozess, kein Server-Prozess blieb zurück.

**Offene Punkte für Stufe 4 und 5** (nicht behoben):
- ~~Der Wächter sollte auch eine Modell-`rueckfrage` prüfen, und/oder der Hinweis sollte die Bewertung der
  Behauptung zeigen.~~ Umgesetzt, offline getestet. Lauf 3 („Euro“) bleibt: Die Rangliste der Behauptung ist
  `mehrdeutig`, der Wächter lässt die Frage deshalb durch. Das ändert nur eine „keine“-Schwelle.
- Eine „keine“-Schwelle und das Trefferkriterium brauchen mehr Daten als die 37 Dev-Fälle; die Schwellen sind
  dünn belegt (siehe Wächter).
- Behauptungen, die die Vorlage ausdrücklich nennen, verlieren gegen Embedding-Scores. Eine Namensübereinstimmung
  könnte in die Rangliste eingehen.
- Der Testteil des Testsets (73 Fälle) ist weiter gesperrt; die Wirkung der Änderungen lässt sich erst in Stufe 5
  messen.

## Grenzen

- **Zusammengesetzte Behauptungen** (mehrere Aussagen, mehrere Vorlagen) gibt es erst in Stufe 5. Der Wächter und
  die Nachsuche gehen von einer Aussage aus.
- **Keine „keine“-Schwelle:** `GUARD_NONE_BELOW` ist `None`; eine fachfremde Behauptung führt nur ohne jeden
  Kandidaten zu „keine“, sonst zu einer Rückfrage.
- **Einzelläufe sind keine Messung:** Fünf Läufe bei einem Modell mit Temperatur 0, 2 von 5 wie erwartet, sagen
  nichts über Quoten.
- **Nur deutsche Behauptungen** (`LANGUAGES = ("de",)`); die Sprache der Behauptung geht nicht an die Suche
  (Stufe 5).
- **Die Belegkontrolle prüft nur, dass ein Zitat im PDF steht**, nicht, ob es zur Behauptung passt.
- Die Schrittliste zeigt bei einer Rückfrage zwei Zeilen mit `?` (Aktion und `ask`); die Oberfläche der Stufe 4
  sollte das bereinigen.

## Tests

Vollständige Suite auf dem Stand dieser Stufe: **850 Tests bestanden**, 158 Subtests, 8 Warnungen (wie in der
Basis). Nach der Suite blieb kein MCP-Server-Prozess zurück.
