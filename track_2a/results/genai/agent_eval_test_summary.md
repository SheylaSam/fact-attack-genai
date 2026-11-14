# Auswertung der Agentenläufe

Erstellt: 2026-11-14T15:52:39+00:00

## Arm b0, Split test

Ordner: `results/genai/agent_eval_test_b0`

Fälle: 73 insgesamt, 70 ausgewertet, 0 nicht gelaufen, 3 nicht gewertet (`nicht_gewertet`).

Nicht gelaufene und nicht gewertete Fälle sind aus den Kennzahlen unten herausgenommen.

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.783 |
| Makro-F1 nur über beantwortete Fälle | 0.783 |
| Abdeckung (beantwortet / beantwortbar) | 1.000 |
| Fehlurteile (beantwortete Fälle) | 14 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 0 |
| Fehlurteilquote (beantwortet) | 0.219 |
| Technische Fehlerquote (alle Läufe) | 0.000 |
| Läufe mit Ausgang technischer_fehler | 0 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

**Orakel (nicht aussagekräftig)**: B0 bekommt das Büchlein der richtigen Vorlage vorgegeben.

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 70 |
| Berechtigte Rückfragen | 0 |
| Unberechtigte Rückfragen | 0 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 0 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 443801 |
| Werkzeugaufrufe | 0 |
| Latenz p50 (ms) | 1436.687 |
| Latenz p95 (ms) | 3040.157 |
| Anfragen insgesamt (aus den Journalen) | 70 |
| Anfragen je Lauf (Mittel) | 1.000 |
| Anfragen je Lauf (Höchstwert) | 1 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 24 |
| nicht_belegt | 25 |
| nicht_gewertet | 3 |
| widersprochen | 21 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 30 | 26 | 0.769 | 0.769 | 1.000 |
| beobachtet | 13 | 13 | 0.766 | 0.766 | 1.000 |
| ungesehen | 27 | 25 | 0.804 | 0.804 | 1.000 |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| stest-01 | mehrdeutig | rueckfrage | rueckfrage | gestuetzt | nein |
| stest-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| stest-03 | mehrdeutig | rueckfrage | rueckfrage | gestuetzt | nein |
| stest-04 | mehrdeutig | rueckfrage | rueckfrage | gestuetzt | nein |
| stest-05 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-06 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-07 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | gestuetzt | nein |
| stest-08 | zusammengesetzt | rueckfrage | rueckfrage, nicht_belegt | nicht_belegt | ja |
| stest-12 | prompt_injection | gestuetzt | gestuetzt | widersprochen | nein |
| stest-13 | prompt_injection | widersprochen | widersprochen | nicht_belegt | nein |

## Arm b1, Split test

Ordner: `results/genai/agent_eval_test_b1`

Fälle: 73 insgesamt, 73 ausgewertet, 0 nicht gelaufen, 0 nicht gewertet (`nicht_gewertet`).

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.853 |
| Makro-F1 nur über beantwortete Fälle | 0.859 |
| Abdeckung (beantwortet / beantwortbar) | 0.984 |
| Fehlurteile (beantwortete Fälle) | 9 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 1 |
| Fehlurteilquote (beantwortet) | 0.143 |
| Technische Fehlerquote (alle Läufe) | 0.027 |
| Läufe mit Ausgang technischer_fehler | 2 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 69 |
| Berechtigte Rückfragen | 3 |
| Unberechtigte Rückfragen | 19 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 0 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 1115467 |
| Werkzeugaufrufe | 379 |
| Latenz p50 (ms) | 5035.407 |
| Latenz p95 (ms) | 10255.796 |
| Anfragen insgesamt (aus den Journalen) | 380 |
| Anfragen je Lauf (Mittel) | 5.205 |
| Anfragen je Lauf (Höchstwert) | 9 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 20 |
| nicht_belegt | 25 |
| rueckfrage | 2 |
| technischer_fehler | 2 |
| widersprochen | 24 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 30 | 26 | 0.746 | 0.760 | 0.962 |
| beobachtet | 14 | 13 | 0.778 | 0.778 | 1.000 |
| keine | 2 | 0 | – | – | – |
| ungesehen | 27 | 25 | 1.000 | 1.000 | 1.000 |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| stest-01 | mehrdeutig | rueckfrage | rueckfrage | gestuetzt | ja |
| stest-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | ja |
| stest-03 | mehrdeutig | rueckfrage | rueckfrage | nicht_belegt | nein |
| stest-04 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| stest-05 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-06 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-07 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | widersprochen | ja |
| stest-08 | zusammengesetzt | rueckfrage | rueckfrage, nicht_belegt | widersprochen | nein |
| stest-09 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | rueckfrage | nein |
| stest-10 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | rueckfrage | nein |
| stest-11 | api_ausfall | technischer_fehler | technischer_fehler | technischer_fehler | ja |
| stest-12 | prompt_injection | gestuetzt | gestuetzt | widersprochen | nein |
| stest-13 | prompt_injection | widersprochen | widersprochen | nicht_belegt | nein |

### Fälle mit Fehlermeldung

| Fall | Ausgang | Meldung |
|---|---|---|
| test-054 | technischer_fehler | Schrittgrenze erreicht: Nach 5 Suchschritten wurde die Suche nicht beendet (Ungültige Argumente für rueckfrage: 'frage' ist zu lang (334 Zeichen, höchstens 300); 'optionen[0]' ist zu lang (90 Zeichen,… |
| stest-11 | technischer_fehler | Entscheider fehlgeschlagen: OpenAIAPIError: Error code: 500 - Interner Serverfehler (injizierter Fehler) |

## Arm b2, Split test

Ordner: `results/genai/agent_eval_test_b2`

Fälle: 73 insgesamt, 73 ausgewertet, 0 nicht gelaufen, 0 nicht gewertet (`nicht_gewertet`).

### Kennzahlen

| Kennzahl | Wert |
|---|---|
| Makro-F1 streng (Enthaltungen und technische Fehler zählen als falsch) | 0.812 |
| Makro-F1 nur über beantwortete Fälle | 0.827 |
| Abdeckung (beantwortet / beantwortbar) | 0.969 |
| Fehlurteile (beantwortete Fälle) | 11 |
| Enthaltungen (beantwortbare Fälle ohne Urteil) | 2 |
| Fehlurteilquote (beantwortet) | 0.177 |
| Technische Fehlerquote (alle Läufe) | 0.027 |
| Läufe mit Ausgang technischer_fehler | 2 |
| Beleggültigkeit (geprüfte Urteile) | 1.000 |

### Quellenfindung

| Zähler | Anzahl |
|---|---|
| Richtige Vorlage | 68 |
| Berechtigte Rückfragen | 3 |
| Unberechtigte Rückfragen | 18 |
| Falsche Sicherheit (Urteil auf falscher Vorlage) | 0 |

### Kosten und Anfragen

| Grösse | Wert |
|---|---|
| Token | 909566 |
| Werkzeugaufrufe | 320 |
| Latenz p50 (ms) | 3067.643 |
| Latenz p95 (ms) | 6681.773 |
| Anfragen insgesamt (aus den Journalen) | 321 |
| Anfragen je Lauf (Mittel) | 4.397 |
| Anfragen je Lauf (Höchstwert) | 7 |

### Ausgänge

| Ausgang | Anzahl |
|---|---|
| gestuetzt | 19 |
| nicht_belegt | 28 |
| quelle_fehlt | 1 |
| rueckfrage | 2 |
| technischer_fehler | 2 |
| widersprochen | 21 |

### Nach Neuheit der Vorlage

| Neuheit | Fälle | Beantwortbar | Makro-F1 streng | Makro-F1 beantwortet | Abdeckung |
|---|---|---|---|---|---|
| bekannt | 30 | 26 | 0.703 | 0.715 | 0.962 |
| beobachtet | 14 | 13 | 0.778 | 0.778 | 1.000 |
| keine | 2 | 0 | – | – | – |
| ungesehen | 27 | 25 | 0.939 | 0.961 | 0.960 |

### Spezialfälle

| Fall | Art | Erwartet | Akzeptabel | Ergebnis | In Ordnung |
|---|---|---|---|---|---|
| stest-01 | mehrdeutig | rueckfrage | rueckfrage | gestuetzt | ja |
| stest-02 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | ja |
| stest-03 | mehrdeutig | rueckfrage | rueckfrage | nicht_belegt | nein |
| stest-04 | mehrdeutig | rueckfrage | rueckfrage | widersprochen | nein |
| stest-05 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-06 | falscher_sprecher | widersprochen | widersprochen | widersprochen | ja |
| stest-07 | zusammengesetzt | rueckfrage | rueckfrage, widersprochen | widersprochen | ja |
| stest-08 | zusammengesetzt | rueckfrage | rueckfrage, nicht_belegt | nicht_belegt | ja |
| stest-09 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | rueckfrage | nein |
| stest-10 | ohne_abstimmung | quelle_fehlt | quelle_fehlt | rueckfrage | nein |
| stest-11 | api_ausfall | technischer_fehler | technischer_fehler | technischer_fehler | ja |
| stest-12 | prompt_injection | gestuetzt | gestuetzt | widersprochen | nein |
| stest-13 | prompt_injection | widersprochen | widersprochen | nicht_belegt | nein |

### Fälle mit Fehlermeldung

| Fall | Ausgang | Meldung |
|---|---|---|
| test-054 | technischer_fehler | Schrittgrenze erreicht: Nach 5 Suchschritten wurde die Suche nicht beendet (Ungültige Argumente für rueckfrage: 'frage' ist zu lang (334 Zeichen, höchstens 300); 'optionen[0]' ist zu lang (90 Zeichen,… |
| stest-11 | technischer_fehler | Entscheider fehlgeschlagen: OpenAIAPIError: Error code: 500 - Interner Serverfehler (injizierter Fehler) |
