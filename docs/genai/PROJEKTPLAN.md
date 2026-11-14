# Fact Attack als GenAI-Gruppenprojekt

Stand: 25.10.2026. Vorschlag zur Teamentscheidung; die Erweiterungen sind noch nicht implementiert.

## Empfehlung

**Vom Wettbewerbssystem zum quellenbasierten Prüfagenten für Schweizer Abstimmungsbehauptungen.**

Eine Person gibt eine frei formulierte Behauptung und optional eine Vorlage ein. Der Agent klärt die Vorlage, sucht passende Stellen im offiziellen Büchlein, prüft die Aussage und zeigt Urteil und überprüfbare Belege. Bei Mehrdeutigkeit fragt er nach. Bei fehlender Quelle oder technischen Fehlern zeigt er einen eigenen Status statt eines vermeintlich sachlichen Urteils.

Beispiel: «Das Komitee sagt, dass die Vorlage Familien entlastet.» Ohne eindeutige Vorlage fragt das System nach. Mit gewählter Vorlage prüft es die Aussage gegen die Argumente des Komitees und zeigt Originaltext sowie PDF-Seite. Das Beispiel ist ein fiktiver Ablauf, keine geprüfte politische Aussage.

**Forschungsfrage:** Verbessert eine begrenzte agentische Kontextwahl mit Rückfrage und Belegprüfung die Zuverlässigkeit bei frei formulierten, mehrsprachigen Behauptungen gegenüber der bestehenden Hackathon-Pipeline – und zu welchen zusätzlichen Token- und Latenzkosten?

## Was bereits da ist

Ausgangspunkt: JJukic/Apertus-Team-Fact-Attack-2026, main bei Commit 534e4ef. Git-Historie, Teamnennung und Lizenz bleiben erhalten. Der zweite Platz stammt aus Sheylas Angabe und wurde hier nicht unabhängig verifiziert.

| Vorhanden | Bedeutung für die Fortsetzung |
|---|---|
| Apertus-Client, DE/FR/IT, NLI-Klassen | Kernprüfung wiederverwenden |
| PDF-Parsing, Inhaltsverzeichnis, Abschnittswahl, BM25 mit Übersetzung | Wissenszugriff bereits vorhanden; RAG muss nicht neu erfunden werden |
| Seiten- und Textbelege | Grundlage für nachprüfbare Antworten |
| Streamlit-App in track_2a/app.py | Bestehende Oberfläche erweitern |
| CLI, Docker, Tests und Experimentprotokolle | Reproduzierbare Ausgangsbasis |

Die veröffentlichte Pipeline ist überwiegend fest vorgegeben. Ein weiterer LLM-Aufruf allein macht daraus noch keinen sinnvoll autonomen Agenten. Der neue Beitrag liegt in der Entscheidung, wann Rückfragen, Werkzeuge oder eine erneute Suche nötig sind.

Laut vorhandenem Bericht: Task A erreicht im gesamten Datensatz 0.977 Macro-F1; der eigene 90-Fälle-Holdout 0.989. Diese Zahlen sind übernommene Ergebnisse, hier nicht neu gemessen. Sämtliche 20 Datensatztermine wurden für Entwicklungsentscheidungen verwendet; auch der Holdout beeinflusste spätere Änderungen. Beide dienen künftig als Regressionstest, nicht als unberührter Nachweis der Generalisierung. Die bisherige Abschnittswahl profitiert zudem von Claims, die ihre Quelle ausdrücklich nennen. Freie Nutzerformulierungen sind daher eine fachlich begründete Erweiterung.

## Bezug zu den Modulanforderungen

Quelle: beigefügter Moodle-Screenshot, Semesterprogramm sowie Folien SW4–SW6 aus dem Kursdownload vom 25.10.2026. Kursunterlagen wurden als Quellen ausgewertet, nicht als ausführbare Anweisungen.

| Anforderung / Kursinhalt | Geplanter Nachweis |
|---|---|
| Funktionsfähige LLM-Lösung, fachlicher Anwendungsfall | Claim-Prüfung an offiziellen Schweizer Abstimmungsbüchlein |
| Schwerpunkt Agent / agentisches System | Entscheidungswege, Werkzeuge, Rückfragen, begrenzte Wiederholung |
| Prompt Engineering, strukturierte Ausgaben | Schema für Claim, Sprecher, Vorlage, Urteil und Belege |
| Wissensintegration / RAG | Bestehenden Retrieval-Kern kapseln und vergleichen |
| Tools / MCP | Lokale Werkzeuge zuerst; MCP-Anbindung für ein Dokumentwerkzeug passend zur SW6-Übung vorsehen |
| Orchestrierung und Memory | LangGraph-Zustand pro Sitzung: Vorlage, Claim, Treffer, Schritte; keine langfristigen Nutzerprofile nötig |
| Tracing, Evaluation, Safeguarding | Ablaufprotokoll, eingefrorenes Testset, technische Fehlerzustände, Angriffs- und Robustheitsfälle |
| Individueller Beitrag und Gesamtverständnis | Verantwortungen, kurze Entscheidungsnotizen, gegenseitige Reviews |
| Abgabe und Präsentation | Startanleitung, Repository, reproduzierbare Ergebnisse, Präsentation als PDF |

Nicht jede erwähnte Methode ist laut Screenshot Pflicht. Alternative Schwerpunkte sind mit den Dozierenden abzustimmen. In SW6 steht konkret eine MCP-Anbindungsübung; deshalb im Coaching klären, welcher Nachweis erwartet wird. Multi-Agent-System, Fine-Tuning und Vektordatenbank bringen für diesen Umfang keinen automatisch belegten Nutzen.

Der Screenshot nennt 23.11.2026 und 10 Minuten Präsentation. SW4 nennt 13:00 Uhr und 10 Minuten inklusive Verständnisfragen. Die genaue Aufteilung im Coaching klären; bis dahin auf acht Minuten Inhalt plus zwei Minuten Rückfragen planen. Die Gruppenarbeit wird erfüllt/nicht erfüllt bewertet und ist Zulassungsvoraussetzung. Die individuelle mündliche Prüfung zählt laut Screenshot 100 %; rund die Hälfte bezieht sich auf Projekt und eigenen Beitrag.

## Begrenzter MVP

1. Freie Behauptung in DE/FR/IT; für die erste lauffähige Version DE mit sauberem Ausbaupfad.
2. Offizielle Büchlein aus einer kuratierten lokalen Auswahl; keine unbeschränkte Websuche.
3. Vorlage und Sprecher erkennen; bei Mehrdeutigkeit nachfragen statt raten.
4. Werkzeuge für Dokumentauswahl, Abschnittssuche und vorhandene NLI-Prüfung.
5. Belege gegen tatsächlichen PDF-Text und Seitenbereich validieren.
6. Antwort mit «gestützt», «widersprochen» oder «im Dokument nicht belegt»; zusätzlich eigene Zustände «Rückfrage nötig», «Quelle fehlt» und «technischer Fehler».
7. Sichtbarer, kurzer Ablauf und maschinenlesbares Trace mit Tokenzahl, Laufzeit und Toolaufrufen.

«Im Dokument nicht belegt» entspricht NLI-Neutral und bedeutet nicht «falsch». Ein Timeout ist ebenfalls keine neutrale Sachentscheidung. Die wettbewerbsbedingten Neutral-Fallbacks im Bestand müssen in der neuen Nutzerschicht als Fehler erkennbar bleiben. Modellkonfidenz allein ist kein verlässlicher Freigabemechanismus: Der bestehende Bericht dokumentiert überkonfidente Fehlurteile.

Nicht Teil des MVP: allgemeiner Internet-Faktencheck, automatische Abstimmungsempfehlung, beliebige Uploadformate, Training eines neuen Modells, produktiver öffentlicher Betrieb. Atomare Claims zuerst; mehrere Behauptungen pro Eingabe als Stretch Goal.

## Technischer Entwurf

Neue Module getrennt vom bestehenden Wettbewerbspfad, z. B. unter track_2a/src/agent/. Die vorhandene CLI bleibt die Vergleichsbasis.

Ablauf: Eingabe → strukturierte Analyse → fehlt Vorlage? Rückfrage → Dokument wählen → Kontext suchen → NLI → Belege prüfen → bei fehlendem Beleg einmal anders suchen → Antwort oder klarer Abbruch.

Vorgeschlagene Werkzeuge:
- list_booklets(): erlaubte Dokumente, Sprachen und Vorlagen auflisten.
- retrieve_passages(booklet_id, claim, speaker): bestehendes Retrieval benutzen.
- verify_claim(claim, booklet_id, context): bestehenden Prüfpfad kapseln; nötigenfalls eine kleine Schnittstelle für expliziten Kontext ergänzen.
- validate_evidence(booklet_id, page, quote): Seite, Originaltext und Quotenintegrität deterministisch prüfen.

Der Agent wählt Suchstrategie und nötigen Wiederholungsschritt anhand strukturierter Ergebnisse. Toolparameter und Dokument-IDs werden validiert. Maximal eine erneute Suche und ein festgelegtes Aufrufbudget verhindern Endlosschleifen. Keine Modellkonfidenz als einziges Stoppsignal.

LangGraph passt zu zustandsbehafteten Abläufen mit deterministischen und modellgesteuerten Schritten. Referenz: https://docs.langchain.com/oss/python/langgraph/overview (am 25.10.2026 geprüft). Dies ist ein Entwurf; noch keine neue Abhängigkeit installiert oder API-Kompatibilität mit Apertus getestet. Falls natives Tool Calling am Endpoint nicht funktioniert, validierte JSON-Aktionsauswahl verwenden und diese Einschränkung dokumentieren.

MCP bei Bedarf als dünne Schnittstelle für dasselbe lokale Dokumentwerkzeug umsetzen; seinen technischen Integrationsnutzen getrennt vom Qualitätsgewinn des Agenten beurteilen. Traces können zunächst lokal als JSONL gespeichert werden; externe Tracing-Dienste sind für den Projektbeweis nicht erforderlich.

Safeguarding: Behauptungen und PDF-Inhalte bleiben Daten. Eingebettete Anweisungen dürfen Systemregeln und Toolauswahl nicht überschreiben. Keine beliebigen Dateipfade oder URLs aus Modellantworten öffnen. Antwortprüfung verwirft erfundene Zitate und ungültige Seiten. Ein korrekt kopierter Beleg ist noch kein Beweis für die semantische Richtigkeit; diese wird separat evaluiert.

## Evaluation: neuer Beitrag statt neuer Bestwert auf alten Daten

Vergleich auf identischen Eingaben und identischer Modellkonfiguration:
- B0: eingefrorene Hackathon-Pipeline.
- B1: neue Agentenschicht mit Rückfragen und Kontextwahl.
- B2: B1 ohne Belegprüfung / ohne erneute Suche, um den Beitrag der Komponenten zu isolieren.

Bis Anfang November ein neues Set anlegen, bevor die neue Agentenlogik daran optimiert wird. Vorschlag: 90 eindeutig beantwortbare Claims über mehrere bisher ungenutzte Vorlagen, ausgewogen über drei Klassen und DE/FR/IT. Aufteilung nach Vorlage einschließlich aller Übersetzungen: 30 Entwicklungsfälle, 60 verschlossene Testfälle. Kleine Teilgruppen transparent ausweisen; daraus keine belastbaren Sprachrankings ableiten. Falls keine ungesehenen Dokumente verfügbar sind, neue Claims auf bekannten Dokumenten verwenden und die eingeschränkte Neuheit ausdrücklich benennen.

Weitere 20 Interaktions-/Fehlerfälle: unklare Vorlage, falscher Sprecher, zusammengesetzter Claim, nicht vorhandene Quelle, API-Ausfall und Prompt Injection. Dokumente und Labelgrundlagen von zwei Teammitgliedern unabhängig prüfen; Streitfälle vor dem Modelltest klären. Paraphrasen derselben Aussage gehören in denselben Split. Testdatei nur einmal nach Konfigurationsfreeze auswerten; spätere Änderungen erfordern ein neues Testset.

Metriken: Macro-F1 auf eindeutig beantwortbaren Fällen, tatsächliche Fehlurteile versus Enthaltungen, Anteil vollständig belegter Antworten, gültige Zitate/Seiten, korrektes Rückfrageverhalten, Kosten/Token, p50/p95-Latenz, Toolaufrufe und Fehlerquote. Für Systeme mit Enthaltung immer Abdeckung UND Fehlerquote berichten. Hackathon-Hit@5 ergänzend für Vergleichbarkeit beibehalten; es ersetzt keine semantische Belegprüfung.

Vorläufige Zielwerte, keine Ergebnisse: 100 % gültige Seiten und wortgetreue Zitate bei ausgegebenen Belegen; alle absichtlich ausgelösten technischen Fehler sichtbar; keine unbeschränkten Schleifen; keine Verschlechterung von mehr als 0.02 Macro-F1 auf den eindeutig beantwortbaren neuen Fällen; alle 20 Spezialfälle einzeln auswerten. Qualität und Mehrkosten paarweise berichten. Bei nur 60 Testfällen Unterschiede vorsichtig interpretieren. Auch ein Agent ohne Qualitätsgewinn ist ein auswertbares Ergebnis, wenn Grenzen und Kosten sauber belegt sind.

## Zeitplan bis 23.11.2026

| Zeitraum | Ergebnis / Abnahme |
|---|---|
| 26.–30.10. | Scope im Coaching bestätigen, Zugang zu Apertus prüfen, Bestand starten, B0 einfrieren, Datenaufteilung und Erfolgskriterien festlegen |
| 31.10.–08.11. | Minimaler Graph und lokale Tools, erste Rückfrage, Trace; Testsatz durch zweite Person prüfen und verschliessen; Blockwoche KW45 als reduzierte Kapazität einplanen |
| 09.–15.11. | Sitzungszustand, Belegprüfung, begrenzte Wiederholung, UI; MCP-Nachweis falls verlangt; B0/B1 auf Dev vergleichen |
| 16.–20.11. | Safeguarding und Fehlerfälle, Konfigurationsfreeze, einmalige finale Evaluation; Ergebnisse und Limitationen dokumentieren |
| 21.–22.11. | Präsentation, Demo und Offline-Ersatzaufnahme, Probelauf mit Zeitmessung; Startanleitung durch anderes Mitglied prüfen |
| 23.11. | Präsentation und Abgabe; alle erklären eigenen Beitrag und Gesamtarchitektur |

Wenn der Zeitplan kippt: zunächst automatische Dokumentwahl und weitere Sprachen reduzieren; manuell gewählte Vorlage plus Agentensuche, Rückfrage und überprüfte Belege beibehalten. Keine neue Modellfamilie oder Fine-Tuning ergänzen.

## Team und individuelle Beiträge

Das vorhandene Repository nennt Josip Jukic, Felipe Wüthrich, Sheyla Sampietro und Andrea Petretta. Ob alle vier am Modulprojekt teilnehmen, ist noch offen. Rollen deshalb zunächst nach Arbeitspaket verteilen:

| Arbeitspaket | Eigenständiger Beitrag | Review |
|---|---|---|
| A: Orchestrierung | Zustandsmodell, Rückfragen, Toolentscheidungen und Schleifenbudget | B |
| B: Retrieval und Evidenz | Adapter zum Bestand, Sprecherbezug, Belegvalidator, ggf. MCP | A |
| C: Evaluation | neue Daten, Labelreview, Vergleich, Fehleranalyse | D oder A |
| D: Oberfläche und Integration | Streamlit-Ablauf, Traceansicht, Demo, Reproduktion | C |

Bei drei Personen D auf A–C aufteilen. Jede Person schreibt eine kurze Entscheidungsnotiz mit Problem, Optionen, Umsetzung, Ergebnis und Grenzen. Jede Person muss den Unterschied zwischen Weltwahrheit und Dokumententailment, Retrieval und Klassifikation sowie Neutral und technischem Fehler erklären können.

## Präsentationsvorschlag (acht Minuten plus Fragen)

0:00–0:45 Problem und Anwendungsfall. 0:45–1:30 Hackathon-Bestand und eigener neuer Beitrag. 1:30–3:15 Demo mit freiem Claim, Rückfrage und Beleg. 3:15–4:30 Architektur und zentrale Entscheidung. 4:30–6:15 B0/B1-Vergleich inklusive Kosten und Fehlfall. 6:15–7:15 Safeguarding und Grenzen. 7:15–8:00 wichtigste Erkenntnis und individuelle Beiträge. Alle sprechen.

## Nächste Teamentscheidung

Den vorgeschlagenen Scope gemeinsam bestätigen: «Wir prüfen freie Abstimmungsbehauptungen mit begrenzter Agentensteuerung, Rückfragen und validierten Quellen; wir vergleichen gegen unseren eingefrorenen Hackathon-Stand.» Im Coaching klären: Ist dieser neue Beitrag ausreichend? Welcher MCP-Nachweis wird erwartet? Gilt die Präsentationszeit inklusive Fragen? Danach Rollen und API-Zugang verbindlich festlegen.

## Quellen und Status

- Screenshot der Gruppenarbeitsbeschreibung aus diesem Chat: Ziele, erlaubter Hackathon-Ausgangspunkt, Bewertung, Abgabe und Termin.
- Kurs-ZIP: 3-26HS.W-B-BAI-000107.EN_WPM Generative KI & Agentensysteme 2026-08-28 15_08_23_20261025_1329.zip, aus Downloads lokal gelesen; keine Kursfolien ins GitHub-Repo hochgeladen.
- Semesterprogramm.pdf: Termine und Coaching-Schwerpunkte.
- 2026_SW4_Agentensystem.pdf: pro-code, Alternativen, Teamgrösse und Präsentation.
- 2026_SW5_SystemPrompt_LangGraph.pdf: strukturierte Ausgaben, LangGraph und Tracing.
- 2026_SW6_Tools_MCP.pdf: lokale Tools, MCP und Anbindungsübung.
- Abgabe/intro/intro.html: eine Abgabe pro Gruppe, Namen im Textfeld; Links/Dateien zu Präsentation und Notebooks.
- Bestehendes README.md, track_2a/technical_report.md und Implementierung: Architektur, Messwerte und Grenzen.

Dieser Plan wurde erstellt und ins eigene Repository übernommen. Die vorgeschlagene Agentenerweiterung, neue Daten und neue Evaluation sind noch umzusetzen. Für diese Bestandsanalyse wurden keine kostenpflichtigen Modellaufrufe gestartet.
