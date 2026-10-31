"""Presentation-only translations. Never translate claims, source quotes or stored records."""
import re

LANGUAGES = {'de': 'Deutsch', 'fr': 'Français', 'it': 'Italiano'}
# German source text | French | Italian
_ROWS = '''Faktencheck|Vérifier|Verifica
Prüfverlauf|Historique|Cronologia
Ergebnisse & Vergleiche|Résultats et comparaisons|Risultati e confronti
So funktioniert’s|Fonctionnement|Come funziona
Was steht wirklich im Büchlein?|Que dit vraiment la brochure ?|Cosa dice davvero l’opuscolo?
Prüfe eine politische Behauptung am offiziellen Abstimmungsbüchlein. Mit Belegstelle zum Nachlesen.|Comparez une affirmation politique à la brochure officielle de votation, avec un passage à consulter.|Confronta un’affermazione politica con l’opuscolo ufficiale di votazione, con un passaggio da consultare.
Was möchtest du prüfen?|Que souhaitez-vous vérifier ?|Cosa vuoi verificare?
Quelle wählen|Choisir la source|Scegli la fonte
Behauptung|Affirmation|Affermazione
Zum Beispiel: Die Vorlage erhöht das Rentenalter auf 67 Jahre.|Par exemple : Le projet relève l’âge de la retraite à 67 ans.|Ad esempio: Il progetto aumenta l’età pensionabile a 67 anni.
Keine eigene Behauptung? Beispiel auswählen|Pas d’affirmation ? Choisir un exemple|Nessuna affermazione? Scegli un esempio
Ein Beispiel füllt Behauptung und Quelle aus. Die Prüfung startest du anschliessend selbst.|Un exemple remplit l’affirmation et la source. Vous lancez ensuite la vérification.|Un esempio compila affermazione e fonte. Avvii poi tu la verifica.
Thema oder Vorlage suchen|Rechercher un thème ou un objet|Cerca un tema o un oggetto
z. B. Mietrecht, Rente, biodiversité|p. ex. bail, retraites, biodiversité|ad es. locazione, pensioni, biodiversità
Sucht lokal in den Vorlagentiteln auf DE, FR und IT; keine Volltextsuche im PDF.|Recherche locale dans les titres DE, FR et IT ; pas dans le texte intégral des PDF.|Ricerca locale nei titoli DE, FR e IT; non nel testo completo dei PDF.
Jahr|Année|Anno
Alle Jahre|Toutes les années|Tutti gli anni
Abstimmung|Votation|Votazione
Sprache des Büchleins|Langue de la brochure|Lingua dell’opuscolo
Vorlage|Objet|Oggetto
Automatisch erkennen|Détection automatique|Riconoscimento automatico
Quellenfilter zurücksetzen|Réinitialiser les filtres|Azzera i filtri
Keine passende Quelle gefunden. Versuche einen anderen Begriff oder setze die Filter zurück. Die bisherige Quelle bleibt ausgewählt.|Aucune source trouvée. Essayez un autre terme ou réinitialisez les filtres. La source précédente reste sélectionnée.|Nessuna fonte trovata. Prova un altro termine o azzera i filtri. La fonte precedente rimane selezionata.
Original bei der Bundeskanzlei öffnen ↗|Ouvrir l’original de la Chancellerie fédérale ↗|Apri l’originale della Cancelleria federale ↗
Sprache der Behauptung|Langue de l’affirmation|Lingua dell’affermazione
Erkennung|Détection|Riconoscimento
Automatisch|Automatique|Automatico
Deutsch, Französisch und Italienisch werden automatisch erkannt. Die Quelle darf eine andere Sprache haben.|L’allemand, le français et l’italien sont détectés automatiquement. La source peut être dans une autre langue.|Tedesco, francese e italiano sono riconosciuti automaticamente. La fonte può essere in un’altra lingua.
Prüfung lokal im Verlauf speichern|Enregistrer la vérification dans l’historique local|Salva la verifica nella cronologia locale
Speichert Behauptung, Quelle und Ergebnis auf diesem Rechner. Der Verlauf ist für Nutzer dieser lokalen App-Installation sichtbar.|Enregistre affirmation, source et résultat sur cet ordinateur. L’historique est visible aux utilisateurs de cette installation locale.|Salva affermazione, fonte e risultato su questo computer. La cronologia è visibile agli utenti di questa installazione locale.
Behauptung prüfen|Vérifier l’affirmation|Verifica l’affermazione
Beim Prüfen werden die Behauptung und ausgewählte Textstellen an Apertus auf CSCS gesendet.|La vérification envoie l’affirmation et des passages sélectionnés à Apertus sur CSCS.|La verifica invia l’affermazione e i passaggi selezionati ad Apertus su CSCS.
Vergleicht die Aussage mit dem Büchlein. Keine allgemeine Wahrheitsprüfung und keine Abstimmungsempfehlung.|Compare l’affirmation à la brochure. Ce n’est ni une vérification générale de vérité ni une recommandation de vote.|Confronta l’affermazione con l’opuscolo. Non è una verifica generale della verità né una raccomandazione di voto.
Weitere Optionen: Suche & eigenes PDF|Autres options : recherche et PDF personnel|Altre opzioni: ricerca e PDF personale
Vektor-Experimente aktivieren|Activer les expériences vectorielles|Attiva gli esperimenti vettoriali
Kontext für Apertus|Contexte pour Apertus|Contesto per Apertus
Standard (Hybrid)|Standard (hybride)|Standard (ibrido)
Ganzes Büchlein|Brochure entière|Opuscolo intero
Vektorsuche (Experiment)|Recherche vectorielle (expérience)|Ricerca vettoriale (esperimento)
BM25 + Vektor (Experiment)|BM25 + vecteurs (expérience)|BM25 + vettori (esperimento)
Experimentelle Suche. Im bisherigen Dev-Vergleich war die Standardstrategie besser.|Recherche expérimentale. La stratégie standard était meilleure dans la comparaison de développement actuelle.|Ricerca sperimentale. La strategia standard era migliore nell’attuale confronto di sviluppo.
Die lokale Vektorsuche ist in dieser Installation noch nicht verfügbar.|La recherche vectorielle locale n’est pas disponible dans cette installation.|La ricerca vettoriale locale non è disponibile in questa installazione.
Eigenes PDF laden|Charger un PDF personnel|Carica un PDF personale
Die Datei wird lokal gespeichert; beim Prüfen werden ausgewählte Textstellen an Apertus gesendet.|Le fichier est enregistré localement ; les passages sélectionnés sont envoyés à Apertus lors de la vérification.|Il file viene salvato localmente; durante la verifica i passaggi selezionati sono inviati ad Apertus.
Dein Ergebnis|Votre résultat|Il tuo risultato
Eingabe geändert · erneut prüfen|Saisie modifiée · vérifier à nouveau|Testo modificato · verifica di nuovo
«Behauptung prüfen» klicken, um das Ergebnis zu aktualisieren.|Cliquez sur « Vérifier l’affirmation » pour actualiser le résultat.|Fai clic su « Verifica l’affermazione » per aggiornare il risultato.
Hier erscheint dein Ergebnis mit der passenden Belegstelle. Gib eine Behauptung ein, wähle die Quelle und starte die Prüfung.|Le résultat et son passage justificatif apparaîtront ici. Saisissez une affirmation, choisissez la source et lancez la vérification.|Qui appariranno il risultato e il relativo passaggio. Inserisci un’affermazione, scegli la fonte e avvia la verifica.
Gestützt|Étayée|Supportata
Nicht geklärt|Non déterminée|Non chiarita
Widerspruch|Contradiction|Contraddizione
— passende Aussage gefunden|— passage correspondant trouvé|— trovato un passaggio corrispondente
— im geprüften Kontext offen|— non déterminée dans le contexte examiné|— non chiarita nel contesto esaminato
— das Büchlein sagt etwas anderes|— la brochure dit autre chose|— l’opuscolo dice altro
Bei gestützten oder widersprochenen Aussagen kannst du die Belegstelle direkt nachlesen.|Pour une affirmation étayée ou contredite, vous pouvez consulter directement le passage justificatif.|Per un’affermazione supportata o contraddetta puoi consultare direttamente il passaggio.
Vom Abstimmungsbüchlein gestützt|Étayée par la brochure de votation|Supportata dall’opuscolo di votazione
Das Büchlein enthält Aussagen, die diese Behauptung stützen.|La brochure contient des passages qui étayent cette affirmation.|L’opuscolo contiene passaggi che supportano questa affermazione.
Im Abstimmungsbüchlein nicht geklärt|Non déterminée dans la brochure de votation|Non chiarita nell’opuscolo di votazione
Im geprüften Kontext wurde keine Aussage gefunden, die diese Behauptung bestätigt oder widerlegt.|Aucun passage confirmant ou réfutant cette affirmation n’a été trouvé dans le contexte examiné.|Nel contesto esaminato non è stato trovato un passaggio che confermi o smentisca questa affermazione.
Widerspricht dem Abstimmungsbüchlein|Contredit la brochure de votation|Contraddice l’opuscolo di votazione
Das Büchlein enthält Aussagen, die dieser Behauptung widersprechen.|La brochure contient des passages qui contredisent cette affirmation.|L’opuscolo contiene passaggi che contraddicono questa affermazione.
Abgleich mit dem offiziellen Büchlein|Comparaison avec la brochure officielle|Confronto con l’opuscolo ufficiale
Belegstellen im Abstimmungsbüchlein|Passages justificatifs dans la brochure|Passaggi a sostegno nell’opuscolo
Details zur Prüfung|Détails de la vérification|Dettagli della verifica
Konfidenzen schätzt Apertus selbst; sie sind nicht kalibriert.|Les niveaux de confiance sont estimés par Apertus et ne sont pas calibrés.|I livelli di confidenza sono stimati da Apertus e non sono calibrati.
Faktencheck-Karte zum Teilen|Carte de vérification à partager|Scheda di verifica da condividere
SVG herunterladen|Télécharger le SVG|Scarica SVG
Dein Prüfverlauf|Votre historique|La tua cronologia
Lokal auf diesem Rechner gespeichert · Öffnen löst keine neue Prüfung aus. Sichtbar für Nutzer dieser App-Installation.|Enregistré sur cet ordinateur · L’ouverture ne lance aucune vérification. Visible aux utilisateurs de cette installation.|Salvata su questo computer · L’apertura non avvia una verifica. Visibile agli utenti di questa installazione.
Ansicht|Vue|Vista
Aktive Prüfungen|Vérifications actives|Verifiche attive
Papierkorb|Corbeille|Cestino
Der lokale Verlauf lässt sich gerade nicht lesen.|L’historique local ne peut pas être lu actuellement.|La cronologia locale non può essere letta al momento.
Der Papierkorb ist leer.|La corbeille est vide.|Il cestino è vuoto.
Noch keine Prüfungen gespeichert. Aktiviere beim Faktencheck die lokale Speicherung.|Aucune vérification enregistrée. Activez l’enregistrement local dans la vérification.|Nessuna verifica salvata. Attiva il salvataggio locale durante la verifica.
Gespeicherte Prüfung|Vérification enregistrée|Verifica salvata
Export & Verlauf verwalten|Exporter et gérer l’historique|Esporta e gestisci la cronologia
JSON enthält Ergebnis und Belegzitate; CSV eine Übersicht. PDF-Dateien sind nicht enthalten.|Le JSON contient le résultat et les citations ; le CSV fournit un aperçu. Les PDF ne sont pas inclus.|Il JSON contiene risultato e citazioni; il CSV una panoramica. I PDF non sono inclusi.
Diese Prüfung als JSON|Cette vérification en JSON|Questa verifica in JSON
Angezeigten Verlauf als CSV|Historique affiché en CSV|Cronologia visualizzata in CSV
Angezeigten Verlauf als JSON|Historique affiché en JSON|Cronologia visualizzata in JSON
Wiederherstellen|Restaurer|Ripristina
In Papierkorb verschieben|Déplacer vers la corbeille|Sposta nel cestino
Einträge im Papierkorb bleiben lokal gespeichert und können wiederhergestellt werden. Die PDF-Datei bleibt erhalten.|Les entrées de la corbeille restent enregistrées localement et peuvent être restaurées. Le PDF est conservé.|Le voci nel cestino restano salvate localmente e possono essere ripristinate. Il PDF viene conservato.
Prüfung wiederhergestellt.|Vérification restaurée.|Verifica ripristinata.
Prüfung in den Papierkorb verschoben.|Vérification déplacée vers la corbeille.|Verifica spostata nel cestino.
Der Eintrag ist nicht mehr vorhanden. Bitte die Ansicht aktualisieren.|L’entrée n’existe plus. Actualisez la vue.|La voce non esiste più. Aggiorna la vista.
Der Verlauf konnte nicht geändert werden.|L’historique n’a pas pu être modifié.|Non è stato possibile modificare la cronologia.
Gespeicherte Demoantwort – bei dieser Prüfung wurde keine neue Liveantwort erzeugt.|Réponse de démonstration enregistrée – aucune nouvelle réponse en direct n’a été produite.|Risposta dimostrativa salvata – non è stata generata una nuova risposta in tempo reale.
Gespeicherte Liveantwort · Keine erneute API-Anfrage beim Öffnen|Réponse en direct enregistrée · Aucun nouvel appel API à l’ouverture|Risposta in tempo reale salvata · Nessuna nuova chiamata API all’apertura
Die ursprüngliche PDF-Datei fehlt oder hat sich verändert. Die gespeicherten Zitate bleiben lesbar; eine Originalvorschau wird nicht angezeigt.|Le PDF original est absent ou a changé. Les citations restent lisibles ; l’aperçu original n’est pas affiché.|Il PDF originale manca o è cambiato. Le citazioni restano leggibili; l’anteprima originale non viene mostrata.
Im damals geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden.|Aucun passage confirmant ou contredisant l’affirmation n’a été trouvé dans le contexte examiné à l’époque.|Nel contesto esaminato allora non è stato trovato un passaggio che confermi o contraddica l’affermazione.
Gespeicherte Details|Détails enregistrés|Dettagli salvati
Lesetext der Seite|Texte de la page|Testo della pagina
Original-PDF herunterladen|Télécharger le PDF original|Scarica il PDF originale
Markiert ist der angezeigte Belegauszug auf der Originalseite.|Le passage affiché est surligné sur la page originale.|Il passaggio visualizzato è evidenziato sulla pagina originale.
Die Belegstelle konnte auf dieser PDF-Seite nicht eindeutig zugeordnet werden. Die Seite wird ohne Markierung angezeigt.|Le passage n’a pas pu être identifié de manière unique sur cette page PDF. La page est affichée sans surlignage.|Non è stato possibile individuare univocamente il passaggio su questa pagina PDF. La pagina viene mostrata senza evidenziazione.
Die Originalseite lässt sich gerade nicht darstellen.|La page originale ne peut pas être affichée actuellement.|La pagina originale non può essere visualizzata al momento.
Die Originalseite lässt sich gerade nicht darstellen. Der Lesetext und das Original-PDF bleiben verfügbar.|La page originale ne peut pas être affichée. Le texte et le PDF original restent disponibles.|La pagina originale non può essere visualizzata. Il testo e il PDF originale restano disponibili.
Das Ergebnis ist verfügbar, konnte aber nicht im lokalen Verlauf gespeichert werden.|Le résultat est disponible, mais n’a pas pu être enregistré dans l’historique local.|Il risultato è disponibile, ma non è stato possibile salvarlo nella cronologia locale.
Die Live-Verbindung zu Apertus ist gerade nicht verfügbar. Angezeigt wird das gespeicherte Ergebnis dieses Beispiels (vorab live auf CSCS geprüft).|La connexion à Apertus est indisponible. Le résultat enregistré de cet exemple est affiché (vérifié auparavant sur CSCS).|La connessione ad Apertus non è disponibile. Viene mostrato il risultato salvato di questo esempio (verificato in precedenza su CSCS).
Apertus prüft die Behauptung auf CSCS…|Apertus vérifie l’affirmation sur CSCS…|Apertus verifica l’affermazione su CSCS…
Januar|janvier|gennaio
Februar|février|febbraio
März|mars|marzo
April|avril|aprile
Mai|mai|maggio
Juni|juin|giugno
Juli|juillet|luglio
August|août|agosto
September|septembre|settembre
Oktober|octobre|ottobre
November|novembre|novembre
Dezember|décembre|dicembre'''
_ROWS += """
Originalseite {v0} öffnen|Ouvrir la page originale {v0}|Apri la pagina originale {v0}
PDF-Seite {v0} · Die gedruckte Seitenzahl kann abweichen.|Page PDF {v0} · Le numéro imprimé peut différer.|Pagina PDF {v0} · Il numero stampato può essere diverso.
Quelle: {v0} · {v1} · Strategie: {v2}|Source : {v0} · {v1} · Stratégie : {v2}|Fonte: {v0} · {v1} · Strategia: {v2}
{v0} von {v1} Terminen gefunden · Suche in Vorlagentiteln|{v0} dates sur {v1} trouvées · Recherche dans les titres|{v0} date su {v1} trovate · Ricerca nei titoli
✓ {v0} geladen|✓ {v0} chargé|✓ {v0} caricato
{v0} Abstimmungstermine · {v1} Sprachfassungen lokal verfügbar|{v0} dates de votation · {v1} versions linguistiques disponibles localement|{v0} date di votazione · {v1} versioni linguistiche disponibili localmente
PDF nicht gefunden: `{v0}`. Bitte `python -m src download` ausführen.|PDF introuvable : `{v0}`. Exécutez `python -m src download`.|PDF non trovato: `{v0}`. Esegui `python -m src download`.
Behauptung: {v0} · Quelle: {v1} · {v2} von {v3} Quellseiten im Kontext|Affirmation : {v0} · Source : {v1} · {v2} pages sources sur {v3} dans le contexte|Affermazione: {v0} · Fonte: {v1} · {v2} pagine fonte su {v3} nel contesto
Beleg {v0} · PDF-Seite {v1}|Passage {v0} · Page PDF {v1}|Passaggio {v0} · Pagina PDF {v1}
Weitere Belegstellen ({v0})|Autres passages ({v0})|Altri passaggi ({v0})
Die Prüfung konnte nicht abgeschlossen werden ({v0}).|La vérification n’a pas pu être terminée ({v0}).|Non è stato possibile completare la verifica ({v0}).
Die Sprache der Oberfläche ändert weder die Behauptung noch die Quelle. Forschungsnotizen, Beispieltitel und Exporte behalten ihre Originalsprache.|La langue de l’interface ne modifie ni l’affirmation ni la source. Les notes de recherche, les titres des exemples et les exports conservent leur langue originale.|La lingua dell’interfaccia non modifica l’affermazione né la fonte. Le note di ricerca, i titoli degli esempi e le esportazioni mantengono la lingua originale.
Technische Forschungsnotizen und gespeicherte Benchmark-Auswertungen sind derzeit auf Deutsch dokumentiert.|Les notes de recherche techniques et les évaluations enregistrées sont actuellement documentées en allemand.|Le note di ricerca tecniche e le valutazioni salvate sono attualmente documentate in tedesco.
Inferenzzeit|Temps d’inférence|Tempo di inferenza
Input-Tokens|Tokens d’entrée|Token in ingresso
Output-Tokens|Tokens de sortie|Token in uscita
Ausgabe im offiziellen OST-Format|Sortie au format officiel OST|Output nel formato ufficiale OST
Reproduzierbarkeit der experimentellen Suche|Reproductibilité de la recherche expérimentale|Riproducibilità della ricerca sperimentale
Im geprüften Kontext wurde keine bestätigende oder widersprechende Belegstelle gefunden.|Aucun passage confirmant ou contredisant l’affirmation n’a été trouvé dans le contexte examiné.|Nel contesto esaminato non è stato trovato un passaggio che confermi o contraddica l’affermazione.
Apertus hat diese Seiten geprüft:|Apertus a examiné ces pages :|Apertus ha esaminato queste pagine:
OFFIZIELLE QUELLEN · DE / FR / IT|SOURCES OFFICIELLES · DE / FR / IT|FONTI UFFICIALI · DE / FR / IT
Offizielle Quellen · DE / FR / IT|Sources officielles · DE / FR / IT|Fonti ufficiali · DE / FR / IT
Noch keine offiziellen Büchlein installiert. Bitte im Projektordner `python -m src.booklet_catalog` ausführen.|Aucune brochure officielle installée. Exécutez `python -m src.booklet_catalog` dans le dossier du projet.|Nessun opuscolo ufficiale installato. Esegui `python -m src.booklet_catalog` nella cartella del progetto.
"""
_ROWS += """
Mehrere Aussagen aus einem Text prüfen|Vérifier plusieurs affirmations d’un texte|Verifica più affermazioni da un testo
Text mit mehreren Aussagen|Texte contenant plusieurs affirmations|Testo con più affermazioni
Lokale Satzvorschläge, keine automatische Faktenauswahl. Bitte Text und Auswahl prüfen; Abkürzungen können falsch getrennt werden.|Suggestions de phrases locales, sans sélection automatique des faits. Vérifiez le texte et la sélection ; les abréviations peuvent être mal découpées.|Suggerimenti locali di frasi, non una selezione automatica dei fatti. Controlla testo e selezione; le abbreviazioni possono essere separate erroneamente.
Bitte kürze den Text auf höchstens 30 Satzvorschläge.|Réduisez le texte à 30 phrases suggérées au maximum.|Riduci il testo a un massimo di 30 frasi suggerite.
Aussagen auswählen|Sélectionner les affirmations|Seleziona le affermazioni
{n} Einzelprüfungen ausgewählt (maximal 5). Jede kann mehrere API-Anfragen auslösen. Alle verwenden die oben gewählte Quelle und Vorlage.|{n} vérifications sélectionnées (maximum 5). Chacune peut déclencher plusieurs appels API. Toutes utilisent la source et l’objet choisis ci-dessus.|{n} verifiche selezionate (massimo 5). Ognuna può generare più chiamate API. Tutte usano la fonte e l’oggetto scelti sopra.
Ausgewählte Aussagen prüfen|Vérifier les affirmations sélectionnées|Verifica le affermazioni selezionate
Die gewählte PDF-Datei ist nicht verfügbar.|Le PDF sélectionné n’est pas disponible.|Il PDF selezionato non è disponibile.
Keine Live-Verbindung konfiguriert. Die Mehrfachprüfung wurde nicht gestartet.|Aucune connexion en direct configurée. La vérification multiple n’a pas démarré.|Nessuna connessione in tempo reale configurata. La verifica multipla non è stata avviata.
Die ausgewählten Aussagen werden einzeln geprüft…|Les affirmations sélectionnées sont vérifiées une par une…|Le affermazioni selezionate vengono verificate una per una…
Ergebnisse der Mehrfachprüfung|Résultats de la vérification multiple|Risultati della verifica multipla
Diese Aussage konnte nicht geprüft werden.|Cette affirmation n’a pas pu être vérifiée.|Non è stato possibile verificare questa affermazione.
Erfolgreiche Prüfungen werden einzeln im Verlauf gespeichert, wenn die lokale Speicherung aktiviert ist. Ein erneuter Start prüft alle ausgewählten Aussagen nochmals.|Les vérifications réussies sont enregistrées individuellement si l’historique local est activé. Un nouveau démarrage vérifie à nouveau toutes les affirmations sélectionnées.|Le verifiche riuscite sono salvate singolarmente se la cronologia locale è attiva. Un nuovo avvio verifica nuovamente tutte le affermazioni selezionate.
Quelle oder Auswahl geändert. Die bisherigen Mehrfachergebnisse passen nicht mehr zur aktuellen Auswahl.|La source ou la sélection a changé. Les résultats précédents ne correspondent plus à la sélection actuelle.|La fonte o la selezione è cambiata. I risultati precedenti non corrispondono più alla selezione attuale.
"""
_ROWS += """
Verlauf durchsuchen|Rechercher dans l’historique|Cerca nella cronologia
Text in Prüfungen suchen|Rechercher du texte dans les vérifications|Cerca testo nelle verifiche
Sucht lokal in Behauptungen, Vorlagentiteln und gespeicherten Belegzitaten.|Recherche locale dans les affirmations, les titres des objets et les citations enregistrées.|Ricerca locale nelle affermazioni, nei titoli degli oggetti e nelle citazioni salvate.
Ergebnis filtern|Filtrer le résultat|Filtra il risultato
Alle Ergebnisse|Tous les résultats|Tutti i risultati
Prüfdatum ab (UTC)|Date de vérification depuis (UTC)|Data della verifica da (UTC)
Prüfdatum bis (UTC)|Date de vérification jusqu’au (UTC)|Data della verifica fino a (UTC)
Das Startdatum liegt nach dem Enddatum. Bitte korrigiere den Zeitraum.|La date de début est après la date de fin. Corrigez la période.|La data iniziale è successiva a quella finale. Correggi il periodo.
{n} von {total} Prüfungen in dieser Ansicht gefunden.|{n} vérifications sur {total} trouvées dans cette vue.|{n} verifiche su {total} trovate in questa vista.
Verlaufsfilter zurücksetzen|Réinitialiser les filtres de l’historique|Azzera i filtri della cronologia
Keine Prüfung passt zu diesen Filtern. Die gespeicherten Einträge bleiben unverändert.|Aucune vérification ne correspond à ces filtres. Les entrées enregistrées restent inchangées.|Nessuna verifica corrisponde a questi filtri. Le voci salvate restano invariate.
"""
_ROWS += """
Nur {n} fehlgeschlagene Aussagen werden erneut geprüft; erfolgreiche Antworten bleiben erhalten.|Seules les {n} affirmations en échec seront revérifiées ; les réponses réussies sont conservées.|Saranno riverificate solo le {n} affermazioni non riuscite; le risposte riuscite restano invariate.
Nur fehlgeschlagene Aussagen erneut prüfen|Revérifier uniquement les échecs|Riprova solo le affermazioni non riuscite
Mehrfachprüfung wird gestartet…|Démarrage de la vérification multiple…|Avvio della verifica multipla…
{done} von {total} Einzelprüfungen abgeschlossen · {claim}|{done} vérifications sur {total} terminées · {claim}|{done} verifiche su {total} completate · {claim}
Aussagen|Affirmations|Affermazioni
Erfolgreich geprüft|Vérifications terminées|Verifiche completate
Fehlgeschlagen|En échec|Non riuscite
Nicht gespeichert|Non enregistrées|Non salvate
Speicherfehler|Erreurs d’enregistrement|Errori di salvataggio
Erfolgreich bedeutet technisch abgeschlossen, unabhängig davon, ob das Büchlein die Aussage stützt, offenlässt oder ihr widerspricht.|Réussie signifie techniquement terminée, que la brochure étaye l’affirmation, ne la détermine pas ou la contredise.|Riuscita significa tecnicamente completata, indipendentemente dal fatto che l’opuscolo supporti l’affermazione, non la chiarisca o la contraddica.
Mehrfachergebnisse exportieren|Exporter les résultats multiples|Esporta i risultati multipli
Mehrfachprüfung als JSON|Vérification multiple en JSON|Verifica multipla in JSON
Mehrfachprüfung als CSV|Vérification multiple en CSV|Verifica multipla in CSV
Der Export enthält auch fehlgeschlagene Aussagen und Versuchszähler. Die Original-PDF-Datei ist nicht enthalten.|L’export contient aussi les affirmations en échec et le nombre de tentatives. Le PDF original n’est pas inclus.|L’esportazione contiene anche le affermazioni non riuscite e il numero di tentativi. Il PDF originale non è incluso.
Prüfversuche: {n}|Tentatives : {n}|Tentativi: {n}
"""
_ROWS += """
Prüfbericht herunterladen (HTML)|Télécharger le rapport (HTML)|Scarica il rapporto (HTML)
Mehrfachprüfung als Druckbericht (HTML)|Rapport imprimable de vérification multiple (HTML)|Rapporto stampabile della verifica multipla (HTML)
Diese Prüfung als Druckbericht (HTML)|Rapport imprimable de cette vérification (HTML)|Rapporto stampabile di questa verifica (HTML)
Gefilterten Verlauf als Druckbericht (HTML)|Rapport imprimable de l’historique filtré (HTML)|Rapporto stampabile della cronologia filtrata (HTML)
Druckfähige Datei ohne externe Inhalte. Im Browser öffnen und bei Bedarf als PDF drucken.|Fichier imprimable sans contenu externe. Ouvrez-le dans le navigateur et imprimez-le en PDF si nécessaire.|File stampabile senza contenuti esterni. Aprilo nel browser e, se necessario, stampalo in PDF.
Antwortart|Type de réponse|Tipo di risposta
Alle Antwortarten|Tous les types de réponse|Tutti i tipi di risposta
Liveantworten|Réponses en direct|Risposte in tempo reale
Demoantworten|Réponses de démonstration|Risposte dimostrative
Sortierung|Tri|Ordinamento
Neueste zuerst|Les plus récentes d’abord|Prima le più recenti
Älteste zuerst|Les plus anciennes d’abord|Prima le più vecchie
Behauptung A–Z|Affirmation A–Z|Affermazione A–Z
Fact Attack – Prüfbericht|Fact Attack – Rapport de vérification|Fact Attack – Rapporto di verifica
Vergleicht Aussagen mit dem Abstimmungsbüchlein. Keine allgemeine Wahrheitsprüfung und keine Abstimmungsempfehlung.|Compare les affirmations à la brochure de votation. Ni vérification générale de vérité ni recommandation de vote.|Confronta le affermazioni con l’opuscolo di votazione. Non è una verifica generale della verità né una raccomandazione di voto.
Bericht erstellt (UTC)|Rapport créé (UTC)|Rapporto creato (UTC)
Prüfungen|Vérifications|Verifiche
Im Browser öffnen und über Drucken ausgeben oder als PDF speichern.|Ouvrez dans le navigateur pour imprimer ou enregistrer en PDF.|Apri nel browser per stampare o salvare in PDF.
Originalzitate bleiben in ihrer Quellensprache. Ein Bericht startet keine neue Prüfung.|Les citations originales conservent leur langue. Un rapport ne lance aucune nouvelle vérification.|Le citazioni originali mantengono la loro lingua. Un rapporto non avvia una nuova verifica.
Historische Momentaufnahme. Demoantworten und technische Fehler sind gesondert gekennzeichnet.|Instantané historique. Les réponses de démonstration et les erreurs techniques sont indiquées séparément.|Istantanea storica. Le risposte dimostrative e gli errori tecnici sono indicati separatamente.
Gespeicherte Demoantwort|Réponse de démonstration enregistrée|Risposta dimostrativa salvata
Gespeicherte Liveantwort|Réponse en direct enregistrée|Risposta in tempo reale salvata
Prüfung fehlgeschlagen|Échec de la vérification|Verifica non riuscita
Technischer Fehler – keine Einordnung|Erreur technique – aucune classification|Errore tecnico – nessuna classificazione
Nicht verfügbar|Non disponible|Non disponibile
Quelle|Source|Fonte
Quellendatum|Date de la source|Data della fonte
Strategie|Stratégie|Strategia
Gespeichert / angezeigt (UTC)|Enregistré / affiché (UTC)|Salvato / visualizzato (UTC)
SHA-256 der Quelle|SHA-256 de la source|SHA-256 della fonte
PDF-Seite|Page PDF|Pagina PDF
Belegstellen|Passages justificatifs|Passaggi a sostegno
Keine Belegstelle in diesem Bericht.|Aucun passage justificatif dans ce rapport.|Nessun passaggio a sostegno in questo rapporto.
Offizielle Originalquelle|Source officielle originale|Fonte ufficiale originale
Das Ergebnis konnte nicht im Verlauf gespeichert werden.|Le résultat n’a pas pu être enregistré dans l’historique.|Non è stato possibile salvare il risultato nella cronologia.
"""
TRANSLATIONS = {parts[0]: {'fr': parts[1], 'it': parts[2]} for row in _ROWS.splitlines() if row.strip() and (parts := row.split('|'))}


def translate(text, language='de'):
    if text.startswith('#'):
        prefix, separator, body = text.partition(' ')
        if separator:
            return prefix + separator + translate(body, language)
    return TRANSLATIONS.get(text, {}).get(language, text)


def translate_static_html(markup, language='de'):
    """Only use on app-authored markup, never documents, quotes or claim cards."""
    def replace(match):
        raw = match.group(1)
        text = raw.strip()
        return '>' + raw.replace(text, translate(text, language)) + '<' if text else match.group(0)
    return re.sub(r'>([^<>]+)<', replace, markup)


def date_label(label, language='de'):
    for month in ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'):
        if month in label:
            return re.sub(r'^(\d+)\.', r'\1', label.replace(month, translate(month, language))) if language != 'de' else label
    return label
