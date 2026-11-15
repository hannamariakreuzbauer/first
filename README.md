# lvtool – Studenten- und Leistungsdaten
Lokales Programm nach dem Pflichtenheft „Studenten- und Leistungsdaten“ (Arbeitsfassung 1.1, Variante 1: ohne Cloud-KI). Es liest die Listen der Universität, das Logbuch und Ihre Punktelisten ein, gleicht alles mit der offiziellen Studentenliste ab, erstellt die Masterliste und bereitet für jeden Studenten einen E-Mail-Entwurf mit dem individuellen Report vor. Es wird nichts versendet und nichts an Dritte übermittelt. Die Originaldateien bleiben unverändert.

## Installation (Windows-PC)
1. Python 3.12 oder neuer von python.org installieren (Häkchen „Add python.exe to PATH“ setzen).
2. Dieses Repository als ZIP herunterladen und entpacken, z. B. nach `C:\lvtool`.
3. Eingabeaufforderung öffnen und ausführen:
```bat
cd C:\lvtool
py -m pip install .[outlook]
```
Danach steht der Befehl `lvtool` zur Verfügung.

## Ordner
```
Eingang\                         frei wählbar, z. B. C:\Users\…\Documents\Lehre\Eingang
  _profile\                      Bewertungsprofile (JSON)
  _vorlagen\                     Report-Vorlagen (Word mit {{PLATZHALTERN}})
  2025W\                         ein Ordner je Semester
    lv_logbuch.xlsx              semesterweites Logbuch (Formular-Export)
    JAT_EDV\                     ein Ordner je Lehrveranstaltung
      TN_LV101147_….xml          Teilnehmerliste der Universität (unverändert)
      export_….csv               Terminliste der Universität (unverändert)
      01_jat_edv_….xlsx          Punkte-Arbeitsmappe (MAIN, TEST, HUE_1)
      _lvtool\                   Arbeitsdaten der App: kurs.json, entscheidungen.json, ausgabe\
```

## Ablauf
```bat
lvtool einrichten "C:\…\Eingang"
lvtool kurs-anlegen "C:\…\Eingang\2025W\JAT_EDV" --profil JAT_EDV_2025-26 --logbuch-lv JAT --lv-titel "JAT & EDV" --vorlage report_jat.docx
lvtool verarbeiten "C:\…\Eingang\2025W\JAT_EDV"
lvtool entwuerfe "C:\…\Eingang\2025W\JAT_EDV" --outlook
```
Im nächsten Semester wird die Lehrveranstaltung kopiert. Übernommen werden Profil, Vorlage, Listenzuordnungen und Strukturentscheidungen, keine personenbezogenen Entscheidungen:
```bat
lvtool kurs-anlegen "C:\…\Eingang\2026W\JAT_EDV" --von "C:\…\Eingang\2025W\JAT_EDV"
```

`verarbeiten` stellt unklare Fälle als nummerierte Frage. Jede Antwort wird in `_lvtool\entscheidungen.json` gespeichert und bei jedem weiteren Lauf wiederverwendet. Mit Enter wird eine Frage übersprungen, mit `x` beendet. Offene Fragen stehen zusätzlich im Blatt „Klärungen“ der Masterliste.

Weitere Befehle:
- `lvtool klaeren KURS`: nur offene Fragen beantworten.
- `lvtool status KURS MATRIKELNUMMER ausgestiegen|unbeurteilt|nachfrist|vorlaeufig|endgueltig|normal`
- `lvtool beobachten EINGANG`: überwacht den Eingangsordner und verarbeitet bei Änderungen neu.
- `lvtool profile EINGANG`, `lvtool profil-kopieren EINGANG ALT NEU`
- `--stichtag JJJJ-MM-TT`: Termine nach dem Stichtag gelten als ausstehend.

## Ergebnisse (in `_lvtool\ausgabe`)
- `masterliste.xlsx` mit den Blättern Master, Anwesenheit, Mitarbeit, Klärungen, Rechenkontrolle (Abweichungen zu Ihrer Arbeitsmappe), Warnungen, Protokoll (alle automatischen Zuordnungen) und Entscheidungen.
- `entwuerfe\*.eml`: Entwürfe, die sich in Outlook öffnen und bearbeiten lassen.
- `serienbrief.xlsx` bzw. `.csv`: Datenquelle für einen Word-Serienbrief.
- Mit `--outlook`: Entwürfe direkt im Ordner „Entwürfe“ des klassischen Outlook. Mit `--konto adresse@…` wird das Uni-Konto gewählt.

## Regeln
- Die offizielle Studentenliste ist die Referenz für die Identität, die Terminliste für die Termine.
- Eindeutige Fälle werden automatisch verarbeitet und protokolliert, z. B. ein Tippfehler in der Wiederholung der Matrikelnummer, wenn Name und E-Mail passen.
- Logbuch-Einträge von Personen, die nicht auf der Studentenliste stehen (andere Gruppen), werden nicht gewertet und im Protokoll ausgewiesen.
- Rückfragen gibt es bei widersprüchlichen Angaben, bei „teilweise“, „mehr …“, Hinweisen im Freitext (z. B. Verspätung, Entschuldigung), bei Mehrfachmeldungen, bei Terminen mit Anmerkung, bei Abweichungen zu den Werten der Arbeitsmappe und bei Fehlterminen im Kompensationsbereich.
- Eine fehlende oder nicht positive Teilleistung führt zu 5 (NG); fehlende Teilleistungen machen die Beurteilung vorläufig.

## Bewertungsprofile
Profile liegen als JSON in `_profile`. Teilleistungstypen: `liste` (Werte aus Punktelisten; `"BLATT:SPALTE"` bindet an ein Blatt), `anwesenheit`, `mitarbeit`, `anwesenheit_mitarbeit`. Weitere Angaben: `max`, `positiv` (`"anteil"`, Zahl, `"erfuellt"`, `"keine"`), `zusatz`, `kontrolle`, `kappen`, Anwesenheitsregel, Mitarbeitsregel und Notenschlüssel. Mitgeliefert werden JAT_EDV_2025-26, JAT_EDV_v2.5, VIAVO_v4.1 und BEISPIEL. Was noch zu bestätigen ist, steht in `zu_bestaetigen` und wird mit `lvtool profile` angezeigt.

## Report-Vorlage
Word-Datei mit Platzhaltern wie `{{VORNAME}}`, `{{NOTE}}`, `{{SUMME}}`, `{{STATUS}}`, `{{HINWEISE}}`, `{{ANWESENHEIT_DETAILS}}`, `{{MITARBEIT_DETAILS}}`, `{{RUECKMELDEFRIST}}` und je Teilleistung `{{ID}}`. Textfelder aus Ihren Listen werden in `kurs.json` unter `textfelder` eingetragen, z. B. `{"KOMMENTAR": ["MAIN:COMM_EXT"]}`. Unbekannte Platzhalter führen zum Abbruch, bevor Entwürfe entstehen. `lvtool einrichten` legt `report_jat.docx` nach dem Muster Ihres JAT-Reports an.

## Test
```bat
py -m pip install .[test]
py -m pytest
```
Die Tests verwenden ausschließlich erfundene Daten (`lvtool/beispiel.py`).
