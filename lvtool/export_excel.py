"""Masterliste als Excel-Arbeitsmappe (neue Datei; Originale bleiben unverändert)."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .pipeline import Ergebnis

KOPF = Font(bold=True)
GELB = PatternFill("solid", fgColor="FFF2CC")
ROT = PatternFill("solid", fgColor="F8CBAD")


def _sheet(wb, title, header, rows, widths=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font = KOPF
    for r in rows:
        ws.append(list(r))
    ws.freeze_panes = "A2"
    for i, h in enumerate(header, start=1):
        w = (widths or {}).get(h) or min(60, max(8, len(str(h)) + 2,
                                               *(len(str(r[i - 1])) + 2 for r in rows[:200] if i - 1 < len(r) and r[i - 1] is not None)))
        ws.column_dimensions[get_column_letter(i)].width = w
    return ws


def write_master(erg: Ergebnis, ziel: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    teile = erg.profil["teilleistungen"]
    textf = list(erg.cfg.get("textfelder") or {})
    header = (["Matrikelnummer", "Familienname", "Vorname", "E-Mail"]
              + [f"{t['id']}" for t in teile] + ["Summe", "Note", "Status", "Anwesenheitspflicht",
                                                  "Fehltermine", "Mitarbeit (Einträge)"] + textf + ["Hinweise"])
    rows = []
    for s in erg.students:
        r = erg.ergebnisse[s.key]
        rows.append([s.matrikel, s.nachname, s.vorname, s.email]
                    + [r.teile[t["id"]].wert for t in teile]
                    + [r.summe, r.note, r.status, r.pflicht, r.abwesend, r.mitarbeit]
                    + [r.texte.get(k, "") for k in textf] + [" ".join(r.hinweise)])
    ws = _sheet(wb, "Master", header, rows, {"Hinweise": 60})
    for i, s in enumerate(erg.students, start=2):
        r = erg.ergebnisse[s.key]
        for j, t in enumerate(teile, start=5):
            te = r.teile[t["id"]]
            if te.positiv is False:
                ws.cell(i, j).fill = ROT
            elif te.status in ("fehlt", "offen", "vorläufig"):
                ws.cell(i, j).fill = GELB

    termine = erg.termine + erg.termine_spaeter
    th = [t.datum.isoformat() for t in termine]
    awl, mia = [], []
    for s in erg.students:
        a = erg.anw.get(s.key, {})
        m = erg.mia.get(s.key, {})
        awl.append([s.matrikel, s.nachname, s.vorname]
                   + [("ausstehend" if t in erg.termine_spaeter else {"A": 1, "S": "1 (V)"}.get(a.get(t.datum), 0))
                      for t in termine] + [erg.ergebnisse[s.key].anwesend, erg.ergebnisse[s.key].abwesend])
        mia.append([s.matrikel, s.nachname, s.vorname] + [m.get(t.datum, 0) for t in termine]
                   + [sum(m.values())])
    _sheet(wb, "Anwesenheit", ["Matrikelnummer", "Familienname", "Vorname"] + th + ["anwesend", "Fehltermine"], awl)
    _sheet(wb, "Mitarbeit", ["Matrikelnummer", "Familienname", "Vorname"] + th + ["Summe"], mia)

    offen = [(q["frage"], " | ".join(lbl for _, lbl in q["optionen"]), q["id"]) for q in erg.offen]
    ws = _sheet(wb, "Klärungen", ["Offene Frage", "Antwortmöglichkeiten", "ID"], offen, {"Offene Frage": 100, "Antwortmöglichkeiten": 50})
    for row in ws.iter_rows(min_row=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    _sheet(wb, "Rechenkontrolle", ["Student", "Feld", "App", "Datei", "Quelle"], erg.kontrolle)
    _sheet(wb, "Warnungen", ["Warnung"], [(w,) for w in erg.warnungen], {"Warnung": 120})
    _sheet(wb, "Protokoll", ["Schritt", "Objekt", "Ergebnis"], erg.protokoll, {"Ergebnis": 90})
    ent = [(v.get("zeit", ""), v.get("frage", ""), str(v.get("antwort")), v.get("kategorie", ""))
           for v in erg.dec.answers.values()]
    _sheet(wb, "Entscheidungen", ["Zeit", "Frage", "Antwort", "Kategorie"], ent, {"Frage": 100})
    ziel.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(ziel)
    except PermissionError:  # Datei in Excel geöffnet
        ziel = ziel.with_name(ziel.stem + "_neu" + ziel.suffix)
        wb.save(ziel)
    return ziel
