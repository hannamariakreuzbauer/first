"""Erfundene Beispieldaten in den Formaten der Universität (keine echten Personen)."""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import openpyxl

from . import course as C

NACHNAMEN = ["Muster", "Beispiel", "Probe", "Testfall", "Platzhalter", "Fiktiv", "Erfunden", "Modell"]
VORNAMEN = ["Anna", "Bernd", "Clara", "David", "Eva", "Felix", "Greta", "Hugo"]
TN_KOPF = ["Gruppe", "lfd.Nr.", "Platz", "Familienname", "Vorname", "Incoming", "Matrikelnummer", "Kennzahl",
           "Studien-ID", "Studium", "Semester im Studium", "Anmeldedatum", "Wartelistenposition", "E-Mail", "Anmerkung"]


def studenten(n=8):
    return [{"nachname": NACHNAMEN[i], "vorname": VORNAMEN[i], "matrikel": f"{12000001 + i * 7:08d}",
             "email": f"{VORNAMEN[i].lower()}.{NACHNAMEN[i].lower()}@stud.example.ac.at", "code": 3000 + i}
            for i in range(n)]


def termine(start=date(2025, 10, 6), n=10):
    return [start + timedelta(weeks=i) for i in range(n)]


def _spreadsheetml(rows):
    def cell(v):
        t = "Number" if isinstance(v, (int, float)) else "String"
        return f'<ss:Cell><ss:Data ss:Type="{t}">{escape(str(v))}</ss:Data></ss:Cell>' if v != "" else "<ss:Cell/>"
    body = "\n".join("<ss:Row>" + "".join(cell(v) for v in r) + "</ss:Row>" for r in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<?mso-application progid="Excel.Sheet"?>\n'
            '<ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
            f'<ss:Worksheet ss:Name="Textexport"><ss:Table>{body}</ss:Table></ss:Worksheet></ss:Workbook>')


def erzeuge(wurzel: Path, semester="2025W", kurs="BSP_LV", seed=1) -> Path:
    rnd = random.Random(seed)
    C.init_root(wurzel)
    kdir = wurzel / semester / kurs
    kdir.mkdir(parents=True, exist_ok=True)
    st = studenten()
    tm = termine()

    rows = [TN_KOPF] + [["Standardgruppe", i + 1, "fix", s["nachname"], s["vorname"], "N", s["matrikel"], "UA 101 ...",
                         f"S{i:07d}", "Diplomstudium Rechtswissenschaften", 3, "2025-09-01 10:00", "", s["email"], ""]
                        for i, s in enumerate(st)]
    (kdir / "TN_LV999999.xml").write_text(_spreadsheetml(rows), encoding="utf-8")

    kopf = "WOCHENTAG;DATUM;VON;BIS;LV_NUMMER;TITEL;LV_ART;ANMERKUNG"
    zeilen = [kopf, f"DI;30.09.2025;12:00;13:30;999999;Beispiel-LV;UV;Vorbereitungstermin nur für LV-Leiter (bitte ignorieren)"]
    zeilen += [f"MO;{d.strftime('%d.%m.%Y')};14:00;15:30;999999;Beispiel-LV;UV;" for d in tm]
    (kdir / "termine_export.csv").write_bytes(("\r\n".join(zeilen) + "\r\n").encode("cp1252"))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SOURCE"
    ws.append(["Zeitstempel", "Familienname", "Vorname", "eMail-Adresse", "Matrikelnummer", "Matrikelnummer Wiederholung",
               "Lehrveranstaltung", "Art des Eintrags", "Datum der betreffenden Stunde",
               "Datum der betreffenden Stunde (Wiederholung)", "Anwesenheit: Ich war anwesend...",
               "Mitarbeit: Ich verdiene ein Mitarbeitsplus...", "Text", "Upload: hier hochladen"])
    for i, s in enumerate(st):
        fehlen = set(rnd.sample(range(len(tm)), i % 5))   # 0 bis 4 Fehltermine
        for j, d in enumerate(tm):
            if j in fehlen:
                continue
            ws.append([datetime.combine(d, datetime.min.time()) + timedelta(hours=16), s["nachname"], s["vorname"],
                       s["email"], int(s["matrikel"]), int(s["matrikel"]), "Beispiel-Lehrveranstaltung [BSP]",
                       "Anwesenheits- und Mitarbeits-Vermerk", datetime.combine(d, datetime.min.time()),
                       datetime.combine(d, datetime.min.time()), 1, rnd.choice([0, 1, 1]), None, None])
    # fremde LV und fremde Person
    ws.append([datetime(2025, 10, 7), "Anders", "Jemand", "jemand@stud.example.ac.at", 19999999, 19999999,
               "Andere Lehrveranstaltung [XYZ]", "Anwesenheits- und Mitarbeits-Vermerk", datetime(2025, 10, 7),
               datetime(2025, 10, 7), 1, 1, None, None])
    ws.append([datetime(2025, 10, 7), "Fremd", "Person", "fremd@stud.example.ac.at", 18888888, 18888888,
               "Beispiel-Lehrveranstaltung [BSP]", "Anwesenheits- und Mitarbeits-Vermerk", datetime(2025, 10, 6),
               datetime(2025, 10, 6), 1, 1, None, None])
    # Tippfehler in der Wiederholung der Matrikelnummer (Name und E-Mail passen)
    s0 = st[0]
    ws.append([datetime(2025, 10, 20, 18), s0["nachname"], s0["vorname"], s0["email"], int(s0["matrikel"]),
               int(s0["matrikel"]) + 1, "Beispiel-Lehrveranstaltung [BSP]", "Anwesenheits- und Mitarbeits-Vermerk",
               datetime(2025, 10, 20), datetime(2025, 10, 20), 1, 0, None, None])
    # Hinweis im Text
    s1 = st[1]
    ws.append([datetime(2025, 11, 3, 18), s1["nachname"], s1["vorname"], s1["email"], int(s1["matrikel"]),
               int(s1["matrikel"]), "Beispiel-Lehrveranstaltung [BSP]", "Anwesenheits- und Mitarbeits-Vermerk",
               datetime(2025, 11, 3), datetime(2025, 11, 3), "teilweise", 0, "musste früher gehen", None])
    wb.save(wurzel / semester / "lv_logbuch.xlsx")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "MAIN"
    ws.append(["FAM_NAME", "PRE_NAME", "COD_NO", "E_MAIL", "TEST_30", "EXTRA_SCORE", "COMM_EXT"])
    for i, s in enumerate(st):
        ws.append([s["nachname"], s["vorname"], s["code"], s["email"], None if i == 7 else 10 + 2 * i,
                   2 if i == 3 else None, "Sehr gute Hausübung." if i == 2 else None])
    ws2 = wb.create_sheet("HUE")
    ws2.append(["Nr.", "Codenummer", "Titel", "SCORE_50", "Begründung"])
    for i, s in enumerate(st):
        ws2.append([i + 1, s["code"], f"Thema {i + 1}", 20 + 4 * i, "Begründung …"])
    wb.save(kdir / "punkte.xlsx")

    if not C.is_course_dir(kdir):
        C.create_course(kdir, profil="BEISPIEL", logbuch_lv=["BSP"], lv_titel="Beispiel-LV",
                        vorlage="report_beispiel.docx", absender_name="Die LV-Leiterin",
                        rueckmeldefrist="Freitag, 2026-02-06", textfelder={"KOMMENTAR": ["MAIN:COMM_EXT"]})
    _beispiel_vorlage(wurzel / C.VORLAGENDIR / "report_beispiel.docx")
    return kdir


def _beispiel_vorlage(ziel: Path):
    import docx
    d = docx.Document()
    d.add_paragraph("{{LV_TITEL}}: Student Report", style="Title")
    d.add_paragraph("Hallo {{VORNAME}},")
    d.add_paragraph("hier ist dein Bewertungsvorschlag. Rückmeldungen bitte bis {{RUECKMELDEFRIST}}.")
    d.add_heading("Das Wichtigste in Kürze", level=2)
    t = d.add_table(rows=0, cols=2)
    for a, b in (("Matrikelnummer:", "{{MATRIKEL}}"), ("Gesamtpunkte:", "{{SUMME}}"), ("Note:", "{{NOTE}}"),
                 ("Status:", "{{STATUS}}"), ("Kommentar:", "{{KOMMENTAR}}")):
        c = t.add_row().cells
        c[0].text, c[1].text = a, b
    d.add_paragraph("{{HINWEISE}}")
    t = d.add_table(rows=0, cols=2)
    for a, b in (("Anwesenheit und Mitarbeit (20):", "{{AW_MIA_20}}"), ("Test (30):", "{{TEST_30}}"),
                 ("Hausübung (50):", "{{HUE_50}}"), ("Zusatzpunkte:", "{{EXTRA_SCORE}}")):
        c = t.add_row().cells
        c[0].text, c[1].text = a, b
    d.add_heading("Anwesenheit", level=3)
    d.add_paragraph("{{ANWESENHEIT_DETAILS}}")
    d.add_paragraph("Gesamt: {{ANWESENHEIT_SUMME}} von {{TERMINE_ANZAHL}} ({{FEHLTERMINE}} Fehltermine)")
    d.add_paragraph("{{ABSENDER_NAME}}")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(ziel))
