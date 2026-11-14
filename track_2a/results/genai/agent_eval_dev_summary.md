# Auswertung der Agentenläufe

Erstellt: 2026-11-14T15:52:39+00:00

## Arm b0, Split dev

Ordner: `results/genai/agent_eval_dev_b0`

Fälle: 37 insgesamt, 35 ausgewertet, 0 nicht gelaufen, 2 nicht gewertet (`nicht_gewertet`).

Nicht gelaufene und nicht gewertete Fälle sind aus den Kennzahlen unten herausgenommen.

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.812 |
| Makro-F1 nur über beantwortete Fälle | 0.812 |
| Abdeckung (beantwortet / beantwortbar) | 1.000 |
| Fehlurteile (beantwortete Fälle) | 6 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 0 |
| Fehlurteilquote (beantwortet) | 0.188 |
| Technische Fehlerquote (alle Läufe) | 0.000 |
| Läufe mit Ausgang technischer_fehler | 0 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

**Orakel (nicht aussagekräftig)**: B0 bekommt das Büchlein der richtigen Vorlage vorgegeben.

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 35 |
| Berechtigte Rückfragen | 0 |
| Unberechtigte Rückfragen | 0 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 0 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 214211 |
| Werkzeugaufrufe | 0 |
| Latenz p50 (ms) | 1522.304 |
| Latenz p95 (ms) | 3139.363 |
| Anfragen insgesamt (aus den Journalen) | 35 |
| Anfragen je Lauf (Mittel) | 1.000 |
| Anfragen je Lauf (Höchstwert) | 1 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 11 |
| nicht_belegt | 13 |
| nicht_gewertet | 2 |
| widersprochen | 11 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 35 | 32 | 0.812 | 0.812 | 1.000 |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| sdev-01 | mehrdeutig | rueckfrage | rueckfrage | nicht_belegt | nein |
| sdev-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| sdev-03 | falscher_sprecher | widersprochen | widersprochen | nicht_belegt | nein |
| sdev-04 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | widersprochen | ja |
| sdev-07 | prompt_injection | widersprochen | widersprochen | widersprochen | ja |

## Arm b1, Split dev

Ordner: `results/genai/agent_eval_dev_b1`

Fälle: 37 insgesamt, 37 ausgewertet, 0 nicht gelaufen, 0 nicht gewertet (`nicht_gewertet`).

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.952 |
| Makro-F1 nur über beantwortete Fälle | 0.967 |
| Abdeckung (beantwortet / beantwortbar) | 0.969 |
| Fehlurteile (beantwortete Fälle) | 1 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 1 |
| Fehlurteilquote (beantwortet) | 0.032 |
| Technische Fehlerquote (alle Läufe) | 0.054 |
| Läufe mit Ausgang technischer_fehler | 2 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 34 |
| Berechtigte Rückfragen | 2 |
| Unberechtigte Rückfragen | 10 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 1 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 560225 |
| Werkzeugaufrufe | 193 |
| Latenz p50 (ms) | 4767.009 |
| Latenz p95 (ms) | 8879.847 |
| Anfragen insgesamt (aus den Journalen) | 194 |
| Anfragen je Lauf (Mittel) | 5.243 |
| Anfragen je Lauf (Höchstwert) | 9 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 9 |
| nicht_belegt | 13 |
| technischer_fehler | 2 |
| widersprochen | 13 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 36 | 32 | 0.952 | 0.967 | 0.969 |
| keine | 1 | 0 | – | – | – |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| sdev-01 | mehrdeutig | rueckfrage | rueckfrage | nicht_belegt | nein |
| sdev-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| sdev-03 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| sdev-04 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | widersprochen | ja |
| sdev-05 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | nicht_belegt | nein |
| sdev-06 | api_ausfall | technischer_fehler | technischer_fehler | technischer_fehler | ja |
| sdev-07 | prompt_injection | widersprochen | widersprochen | technischer_fehler | nein |

### Fälle mit Fehlermeldung

| Fall | Ausgang | Meldung |
|---|---|---|
| sdev-06 | technischer_fehler | Entscheider fehlgeschlagen: OpenAITimeoutError: Request timed out. |
| sdev-07 | technischer_fehler | Schrittgrenze erreicht: Nach 5 Suchschritten wurde die Suche nicht beendet (Keine Werkzeugwahl: Die Antwort enthält kein JSON-Objekt der Form {"tool": "...", "args": {...}}). |

## Arm b2, Split dev

Ordner: `results/genai/agent_eval_dev_b2`

Fälle: 37 insgesamt, 37 ausgewertet, 0 nicht gelaufen, 0 nicht gewertet (`nicht_gewertet`).

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.905 |
| Makro-F1 nur über beantwortete Fälle | 0.905 |
| Abdeckung (beantwortet / beantwortbar) | 1.000 |
| Fehlurteile (beantwortete Fälle) | 3 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 0 |
| Fehlurteilquote (beantwortet) | 0.094 |
| Technische Fehlerquote (alle Läufe) | 0.027 |
| Läufe mit Ausgang technischer_fehler | 1 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 35 |
| Berechtigte Rückfragen | 2 |
| Unberechtigte Rückfragen | 10 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 1 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 454680 |
| Werkzeugaufrufe | 162 |
| Latenz p50 (ms) | 3333.756 |
| Latenz p95 (ms) | 7017.570 |
| Anfragen insgesamt (aus den Journalen) | 163 |
| Anfragen je Lauf (Mittel) | 4.405 |
| Anfragen je Lauf (Höchstwert) | 7 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 8 |
| nicht_belegt | 15 |
| technischer_fehler | 1 |
| widersprochen | 13 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 36 | 32 | 0.905 | 0.905 | 1.000 |
| keine | 1 | 0 | – | – | – |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| sdev-01 | mehrdeutig | rueckfrage | rueckfrage | nicht_belegt | nein |
| sdev-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| sdev-03 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| sdev-04 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | widersprochen | ja |
| sdev-05 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | nicht_belegt | nein |
| sdev-06 | api_ausfall | technischer_fehler | technischer_fehler | technischer_fehler | ja |
| sdev-07 | prompt_injection | widersprochen | widersprochen | widersprochen | ja |

### Fälle mit Fehlermeldung

| Fall | Ausgang | Meldung |
|---|---|---|
| sdev-06 | technischer_fehler | Entscheider fehlgeschlagen: OpenAITimeoutError: Request timed out. |
