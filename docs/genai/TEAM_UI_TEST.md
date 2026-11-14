# Teamtest: Fact Attack

Vorbereiteter manueller Testplan, noch kein durchgeführter Teamtest.
Dauer: etwa 15–20 Minuten pro Person. Nutzt die lokale App auf 127.0.0.1:8501.
Die ersten Aufgaben benötigen keine neue Modellprüfung. Vor echten Prüfungen
Anzahl und Quelle bewusst wählen; vorhandene Beispiele können einen API-Aufruf
starten, sobald der Prüfbutton gedrückt wird.

| Aufgabe | Erwartetes Verhalten | Beobachtung / Problem |
|---|---|---|
| DE → FR → IT wechseln, eigene Behauptung vorher eingeben | Text, Quelle und Strategie bleiben erhalten | |
| Quelle über „Mietrecht“ suchen | November 2024; Original-Link öffnet Bundeskanzlei | |
| Leere Suche / unbekanntes Thema ausprobieren | Klarer Nulltrefferhinweis, Filter zurücksetzbar | |
| Bereits gespeicherte Prüfung öffnen | Keine neue NLI-Anfrage; Demo/Live erkennbar | |
| Verlauf nach Text, Ergebnis, UTC-Datum und Antwortart filtern | Trefferzahl und Auswahl passen zusammen | |
| Älteste zuerst und Behauptung A–Z auswählen | Reihenfolge nachvollziehbar; Originaldaten unverändert | |
| Eintrag in Papierkorb verschieben und zurückholen | Wiederherstellbar; Quelldatei bleibt erhalten | |
| Einzelbericht auf DE/FR/IT herunterladen | Beschriftung übersetzt, Originalzitate unverändert | |
| Gefilterten Verlauf als HTML-Bericht herunterladen | Nur sichtbare Treffer; Quelle und Zeitangaben enthalten | |
| HTML-Datei im Browser öffnen, Druckvorschau ansehen | Lesbar in A4, auch lange Zitate; kein Inhalt überlappt | |
| Zwei Aussagen lokal auswählen, noch nicht starten | Anzahl und gemeinsame Quelle vor Start sichtbar | |
| Optional: bewusste Liveprüfung starten | Jeder Erfolg / Fehler getrennt, Fortschritt pro Aussage | |
| Bei Teilfehler nur Fehler wiederholen | Erfolgreiche Antworten und Verlaufseinträge nicht doppelt | |
| Batch-Bericht mit Teilfehler exportieren | Fehler ausdrücklich ohne inhaltliche Einordnung | |

## Fragen nach dem Test

- War klar, dass das Ergebnis nur den Bezug zum Büchlein beschreibt?
- War eine Demoantwort eindeutig von einer neuen Liveantwort unterscheidbar?
- Waren Prüfzeitpunkt (UTC) und Abstimmungstermin verständlich getrennt?
- War vor dem Start klar, welche Quelle und wie viele Aussagen geprüft werden?
- Welche Stelle erforderte Erklärung oder führte zu einem falschen Klick?

Erfasst pro Problem: Aufgabe, erwartetes Verhalten, tatsächliches Verhalten,
UI-Sprache, Bildschirmgrösse und Schwere (blockierend / störend / kosmetisch).
Keine Schlüssel oder privaten Behauptungen in gemeinsam geteilte Screenshots
oder Fehlerberichte übernehmen.

Person: __________  Datum: __________  Version/Commit: __________
