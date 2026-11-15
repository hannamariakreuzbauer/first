# lvtool – Studenten- und Leistungsdaten
Eigenständige lokale App nach dem Pflichtenheft „Studenten- und Leistungsdaten“ (Arbeitsfassung 1.1, Variante 1: ohne Cloud-KI) für beliebige Lehrveranstaltungen und Semester. Sie liest die Listen der Universität, das Logbuch und Ihre Punktelisten ein, gleicht alles mit der offiziellen Studentenliste ab, erstellt die Masterliste und bereitet für jeden Studenten einen E-Mail-Entwurf mit dem individuellen Report vor. Es wird nichts versendet und nichts an Dritte übermittelt. Die Originaldateien bleiben unverändert.

## Die App (Windows-PC)
`LV-Tool.exe` ist eine einzelne Programmdatei. Ein Doppelklick öffnet ein eigenes Fenster (Google Chrome im App-Modus, ohne Adressleiste). Eine Installation von Python ist nicht nötig. Die Datei wird bei jeder Änderung automatisch auf GitHub gebaut: Repository → „Actions“ → neuester Lauf „Windows-App“ → Artefakt „LV-Tool-Windows“ herunterladen und entpacken.

Beim ersten Start meldet Windows SmartScreen eine unbekannte App, weil die Datei nicht signiert ist: „Weitere Informationen“ → „Trotzdem ausführen“.

Die App arbeitet nur lokal. Sie ist ausschließlich vom eigenen Rechner aus erreichbar (127.0.0.1, mit Zugriffsschlüssel) und überträgt keine Daten.

## Ordner
```
Eingang\                         frei wählbar
  _profile\                      Bewertungsprofile (legt die App an)
  _vorlagen\                     Report-Vorlagen (Word mit {{PLATZHALTERN}})
  2026W\                         ein Ordner je Semester
    logbuch.xlsx                 semesterweites Logbuch (Formular-Export)
    MEINE_LV\                    ein Ordner je Lehrveranstaltung
      Teilnehmerliste.xml        Listen der Universität, unverändert
      Termine.csv
      Punkte.xlsx                Ihre Punktelisten
      _lvtool\                   Arbeitsdaten der App (Einstellungen, Entscheidungen, Ausgabe)
```
Dateinamen sind frei. Die App erkennt die Listen am Inhalt und fragt bei Unklarheit einmal nach.

## Ablauf je Semester
1. „＋ Semester“ und „＋ Lehrveranstaltung“. Für eine bestehende LV „Kopie des Vorsemesters“ wählen: Bewertung, Vorlage und Listenzuordnungen werden übernommen, personenbezogene Entscheidungen nicht.
2. Listen der Universität und Punktelisten unverändert in den LV-Ordner legen, das Logbuch in den Semesterordner. Die App verarbeitet neue Dateien automatisch.
3. Reiter „Bewertung“: Teilleistungen, Höchstpunkte, Quelle (Spalte aus Ihren Listen zur Auswahl), Positivitätsregel, Anwesenheits- und Mitarbeitsregeln, Notenschlüssel. „Als neues Profil speichern“ erzeugt eine eigene Fassung für diese LV.
4. Reiter „Klärungen“: unklare Fälle per Klick entscheiden. Jede Antwort wird gespeichert und lässt sich unter „Protokoll“ zurücknehmen.
5. Reiter „Ergebnisse“: Punkte, Note, Status; Status je Student festlegbar (ausgestiegen, unbeurteilt, Nachfrist …). Die Masterliste liegt als Excel-Datei vor.
6. Reiter „E-Mail-Entwürfe“: Vorschau je Student, dann „Entwürfe in Outlook anlegen“ (klassisches Outlook) oder als .eml-Dateien und Serienbrief-Datei speichern. Es wird nichts versendet.

## Ergebnisse (im LV-Ordner unter `_lvtool\ausgabe`)
- `masterliste.xlsx` mit den Blättern Master, Anwesenheit, Mitarbeit, Klärungen, Rechenkontrolle, Warnungen, Protokoll und Entscheidungen.
- `entwuerfe\*.eml`, `serienbrief.xlsx` und `serienbrief.csv`.

## Regeln
- Die offizielle Studentenliste ist die Referenz für die Identität, die Terminliste für die Termine.
- Eindeutige Fälle werden automatisch verarbeitet und protokolliert, z. B. ein Tippfehler in der Wiederholung der Matrikelnummer, wenn Name und E-Mail passen.
- Logbuch-Einträge von Personen, die nicht auf der Studentenliste stehen (andere Gruppen), werden nicht gewertet und im Protokoll ausgewiesen.
- Rückfragen gibt es bei widersprüchlichen Angaben, bei „teilweise“, „mehr …“, Hinweisen im Freitext (z. B. Verspätung, Entschuldigung), bei Mehrfachmeldungen, bei Terminen mit Anmerkung, bei Abweichungen zu den Werten der Arbeitsmappe und bei Fehlterminen im Kompensationsbereich.
- Eine fehlende oder nicht positive Teilleistung führt zu 5 (NG); fehlende Teilleistungen machen die Beurteilung vorläufig.

## Report-Vorlage
Die mitgelieferte `report_standard.docx` passt zu jedem Bewertungsprofil: `{{TABELLE_TEILLEISTUNGEN}}` und `{{NOTENSCHLUESSEL}}` werden aus dem Profil erzeugt. Der Text lässt sich in Word frei ändern. Weitere Platzhalter: `{{LV_TITEL}}`, `{{NACHNAME}}`, `{{VORNAME}}`, `{{MATRIKEL}}`, `{{STUDIUM}}`, `{{EMAIL}}`, `{{SUMME}}`, `{{NOTE}}`, `{{STATUS}}`, `{{HINWEISE}}`, `{{KOMMENTAR}}`, `{{ANWESENHEIT_DETAILS}}`, `{{ANWESENHEIT_SUMME}}`, `{{FEHLTERMINE}}`, `{{TERMINE_ANZAHL}}`, `{{MITARBEIT_DETAILS}}`, `{{MITARBEIT_SUMME}}`, `{{RUECKMELDEFRIST}}`, `{{ABSENDER_NAME}}` sowie je Teilleistung ihr Kürzel. Unbekannte Platzhalter führen zum Abbruch, bevor Entwürfe entstehen.

## Für Entwickler
Start ohne .exe: `py -m pip install .[outlook]`, dann `lv-tool`. Die Kommandozeile `lvtool` bietet dieselben Funktionen (`lvtool --help`).

## Test
```bat
py -m pip install .[test]
py -m pytest
```
Die Tests verwenden ausschließlich erfundene Daten (`lvtool/beispiel.py`).
