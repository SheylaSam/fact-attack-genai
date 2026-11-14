# Neue Testdaten vor Optimierung verschliessen

Alle bisher vorhandenen Datensatztermine einschliesslich September-Holdout sind
Dev/Regression. Es wurde kein neuer unabhängiger Testdatensatz erzeugt.

Die Datenverantwortliche sammelt neue echte Fälle mit geprüftem Label und physischen
PDF-Belegseiten. Mindestfelder pro JSONL-Zeile:
`id`, `claim`, `claim_language`, `reference_language`, `booklet_file`, `vote`,
`entailment_label` (0/1/2), `reference_string`, `gold_evidence_pages` (1-basierte
physische PDF-Seiten), `proposal_group`, `template_group`, `paraphrase_family`.
Keine automatisch erfundenen Labels übernehmen. Unbelegbare/mehrdeutige Fälle zur
manuellen Klärung zurückstellen. Neutralfälle benötigen eine gesonderte Annotation,
ob überhaupt ein relevanter Beleg existiert; leere Seitenlisten sind keine Recall-Nullen.

Aufteilung erfolgt **vor** Optimierung: verbundene Gruppen derselben Vorlage,
Vorlagenfamilie und Paraphrasenfamilie zusammenhalten. Alle Übersetzungen und
Sprachvarianten bleiben bei ihrer Ursprungsgruppe. Die Sprache ist ein
Stratifizierungsmerkmal, kein zufälliger Split je Zeile. Gruppenüberlappungen über
mehrere Felder transitiv zusammenführen; dieselbe Vorlage darf nie Dev und Test
beliefern. Neue Vorlagen bevorzugen, deren Inhalt die Entwicklung nicht beeinflusst hat.

Die Datenverantwortliche legt Testdatei und Labels ausserhalb des Entwicklungscheckouts
in einem zugriffsbeschränkten Ordner ab; sie publiziert nur Anzahl, Gruppen-/Sprachverteilung
und SHA-256 von JSONL, PDFs und Splitmanifest. Beispiel: `shasum -a 256 test.jsonl`.
Ein Hash dokumentiert Integrität, ersetzt aber keine Zugriffssperre. Datum, Reviewer,
Labelrichtlinie und geplante Metriken im Manifest festhalten. Keine Testlabels in Git.

Erst nach Einfrieren von Retrievalkonfiguration, Prompt und Modellrevision wird genau
ein gepaarter Vergleich ausgeführt. Jede anschliessende Optimierung macht diesen
Test zu beobachteten Daten. Der aktuelle Evaluator bezeichnet jeden Lauf bewusst
als Dev/Regression und beansprucht keine versiegelte Testauswertung.
