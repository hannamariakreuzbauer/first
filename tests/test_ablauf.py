from datetime import date
from pathlib import Path

import pytest

from lvtool import course as C
from lvtool import report as R
from lvtool.beispiel import erzeuge, studenten
from lvtool.decisions import qid
from lvtool.io_tables import parse_date_candidates, parse_number, read_tables
from lvtool.pipeline import note_fuer, run

STICHTAG = date(2026, 1, 31)
NOTEN = C.load_profile(Path("."), "JAT_EDV_2025-26")["notenschluessel"]


@pytest.mark.parametrize("p,n", [(0, "5 (NG)"), (49.99, "5 (NG)"), (50, "4 (GN)"), (62.4, "4 (GN)"), (62.5, "3 (BE)"),
                                 (74.9, "3 (BE)"), (75, "2 (GU)"), (87.49, "2 (GU)"), (87.5, "1 (SG)"), (105, "1 (SG)")])
def test_notenschluessel(p, n):
    assert note_fuer(p, NOTEN) == n


def test_werte():
    assert parse_number("27,5") == 27.5
    assert parse_number("1.234,5") == 1234.5
    assert parse_date_candidates("10/13/2025") == [date(2025, 10, 13)]
    assert set(parse_date_candidates("03/04/2026")) == {date(2026, 4, 3), date(2026, 3, 4)}


def beantworte(dec, erg, regel):
    n = 0
    for q in erg.offen:
        wert = regel(q)
        if wert is not None:
            dec.answer(q["id"], wert)
            n += 1
    return n


def standard(q):
    f = q["frage"]
    if f.startswith("Termin 2025-09-30"):
        return "nein"
    if "Eintragsart" in f:
        return "vermerk"
    if "Anwesenheit „teilweise“" in f:
        return "sonder"
    if "Hinweis im Text" in f:
        return "normal"
    if "Fehltermine" in f:
        return "ja"
    return q["optionen"][0][0]


@pytest.fixture()
def kurs(tmp_path):
    return erzeuge(tmp_path)


def lauf_bis_fertig(kurs):
    dec = C.decisions_for(kurs)
    for _ in range(10):
        erg = run(kurs, dec, STICHTAG)
        if not erg.offen:
            dec.save()
            return erg
        assert beantworte(dec, erg, standard)
    raise AssertionError("Fragen werden nicht weniger")


def test_ablauf(kurs, tmp_path):
    erg = lauf_bis_fertig(kurs)
    assert len(erg.students) == 8
    assert len(erg.termine) == 10                     # Vorbereitungstermin nicht gezählt
    st = {s.matrikel: s for s in erg.students}
    fremd = [p for p in erg.protokoll if p[0] == "Nicht in Studentenliste"]
    assert fremd, "fremde Person muss protokolliert, nicht stillschweigend verworfen werden"
    roh = studenten()
    # Student 3 hat Zusatzpunkte, Student 7 keinen Test -> negativ, vorläufig
    r7 = erg.ergebnisse[st[roh[7]["matrikel"]].key]
    assert r7.teile["TEST_30"].status == "fehlt" and r7.note == "5 (NG)" and r7.status == "vorläufig"
    r3 = erg.ergebnisse[st[roh[3]["matrikel"]].key]
    assert r3.teile["EXTRA_SCORE"].wert == 2
    # Hausübung über Codenummer zugeordnet
    assert all(erg.ergebnisse[s.key].teile["HUE_50"].wert is not None for s in erg.students)
    # Summe = Teilleistungen
    for r in erg.ergebnisse.values():
        assert r.summe == sum(t.wert or 0 for t in r.teile.values())
    # Anwesenheit/Mitarbeit: max 20, Hälfte für erfüllte Anwesenheitspflicht
    for r in erg.ergebnisse.values():
        te = r.teile["AW_MIA_20"]
        if r.pflicht == "erfüllt":
            assert 10 <= te.wert <= 20
        else:
            assert te.wert <= 10 and te.positiv is False

    from lvtool.export_excel import write_master
    ziel = write_master(erg, tmp_path / "out" / "master.xlsx")
    blaetter = {t.sheet for t in read_tables(ziel)}
    assert {"Master", "Anwesenheit", "Mitarbeit"} <= blaetter

    mails = R.entwuerfe(erg)
    assert len(mails) == 8
    m = mails[0]
    assert "{{" not in m["html"] and "{{" not in m["text"]
    assert m["student"].vorname in m["text"]
    emls = R.schreibe_eml(mails, tmp_path / "eml")
    assert b"X-Unsent: 1" in emls[0].read_bytes()


def test_entscheidungen_bleiben_und_kopie_ohne_personen(kurs, tmp_path):
    lauf_bis_fertig(kurs)
    alt = C.decisions_for(kurs)
    assert any(v["kategorie"] == "person" for v in alt.answers.values())
    neu_dir = kurs.parent.parent / "2026S" / kurs.name
    C.create_course(neu_dir, von=kurs)
    neu = C.decisions_for(neu_dir)
    assert neu.answers and all(v["kategorie"] == "struktur" for v in neu.answers.values())
    # das neue Semester bekommt eine eigene Kopie des Bewertungsprofils
    assert C.load_course(neu_dir)["profil"] == "BSP_LV_2026S"
    root = kurs.parent.parent
    alt_p, neu_p = C.load_profile(root, "BEISPIEL"), C.load_profile(root, "BSP_LV_2026S")
    assert neu_p["teilleistungen"] == alt_p["teilleistungen"] and neu_p["notenschluessel"] == alt_p["notenschluessel"]


def test_status_ausgestiegen(kurs):
    erg = lauf_bis_fertig(kurs)
    s = erg.students[0]
    dec = C.decisions_for(kurs)
    dec.answer(qid("status", s.key), "ausgestiegen")
    erg = run(kurs, dec, STICHTAG)
    assert erg.ergebnisse[s.key].note == "ausgestiegen"


def test_originale_unveraendert(kurs):
    vorher = {p: p.read_bytes() for p in kurs.parent.rglob("*") if p.is_file() and "_lvtool" not in p.parts}
    lauf_bis_fertig(kurs)
    nachher = {p: p.read_bytes() for p in vorher}
    assert vorher == nachher


def test_standardvorlage_passt_zu_jedem_profil(kurs, tmp_path):
    erg = lauf_bis_fertig(kurs)
    for pid in C.list_profiles(tmp_path):
        erg.profil = C.load_profile(tmp_path, pid)
        ph = R.platzhalter_der_vorlage(R.vorlage_pfad(erg))
        assert "TABELLE_TEILLEISTUNGEN" in ph and "NOTENSCHLUESSEL" in ph
    m = R.entwuerfe(lauf_bis_fertig(kurs))[0]
    assert "Test (30):" in m["text"] and "ab 87,5 Punkten: 1 (SG)" in m["text"]
    assert "<table" in m["html"] and "_tabelle_html" not in m["html"]


def test_app_api(kurs, tmp_path, monkeypatch):
    from lvtool import app
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    app.CACHE.clear()
    app.aktion("wurzel", {"pfad": str(tmp_path)})
    z = app.zustand()
    assert z["semester"][0]["kurse"][0]["eingerichtet"]
    e = app.aktion("verarbeiten", {"pfad": str(kurs)})
    assert e["offen"]
    for _ in range(10):
        if not e["offen"]:
            break
        for q in e["offen"]:
            app.aktion("antwort", {"pfad": str(kurs), "id": q["id"], "wert": standard(q)})
        e = app.aktion("verarbeiten", {"pfad": str(kurs)})
    assert not e["offen"] and len(e["studenten"]) == 8
    # Bewertung in der App ändern: neues Profil mit anderer Teilleistung
    p = app.aktion("profil_laden", {"id": "BEISPIEL"})["profil"]
    p["id"] = "NEU_2026"
    p["teilleistungen"] = [t for t in p["teilleistungen"] if t["id"] != "EXTRA_SCORE"]
    app.aktion("profil_speichern", {"profil": p, "neu": True})
    app.aktion("kurs_speichern", {"pfad": str(kurs), "cfg": {"profil": "NEU_2026"}})
    e = app.aktion("verarbeiten", {"pfad": str(kurs)})
    assert [t["id"] for t in e["teile"]] == ["AW_MIA_20", "TEST_30", "HUE_50"]
    spalten = [s for q in app.aktion("quellen", {"pfad": str(kurs)}) for s in q["spalten"]]
    assert "MAIN:TEST_30" in spalten and "HUE:SCORE_50" in spalten
    r = app.aktion("entwuerfe", {"pfad": str(kurs), "art": "datei"})
    assert r["eml"] == 8
    with pytest.raises(PermissionError):
        app.aktion("oeffnen", {"pfad": str(tmp_path.parent)})


def test_logbuch_neu_exportiert_entscheidungen_bleiben(kurs):
    """Neue Zeilen am Anfang und anderer Dateiname dürfen beantwortete Fragen nicht zurückbringen."""
    import datetime as dt

    import openpyxl
    lauf_bis_fertig(kurs)
    lb = kurs.parent / "lv_logbuch.xlsx"
    wb = openpyxl.load_workbook(lb)
    ws = wb["SOURCE"]
    ws.insert_rows(2)
    for c, v in enumerate([dt.datetime(2025, 10, 1), "Neu", "Person", "neu@x.at", 17777777, 17777777, "Andere [XYZ]",
                           "Anwesenheits- und Mitarbeits-Vermerk", dt.datetime(2025, 10, 6), dt.datetime(2025, 10, 6), 1, 0], 1):
        ws.cell(2, c, v)
    wb.save(kurs.parent / "logbuch_export_neu.xlsx")
    lb.unlink()
    erg = run(kurs, C.decisions_for(kurs), STICHTAG)
    assert not erg.offen


def test_ein_logbuch_fuer_mehrere_lv(tmp_path):
    """Zwei LV im selben Semester teilen sich ein Logbuch; jede sieht nur ihre Einträge und Studenten."""
    import datetime as dt

    import openpyxl
    a = erzeuge(tmp_path, kurs="LV_A")
    b = tmp_path / "2025W" / "LV_B"
    b.mkdir()
    # LV_B: eigene Studentenliste (CSV) und Termine, Einträge im selben Logbuch unter [BBB]
    (b / "teilnehmer.csv").write_text("Familienname;Vorname;Matrikelnummer;E-Mail\nZweit;Zora;12999001;zora@x.at\n"
                                      "Zweit;Zeno;12999002;zeno@x.at\n", encoding="utf-8")
    (b / "termine.csv").write_text("DATUM;VON;BIS\n07.10.2025;10:00;11:30\n14.10.2025;10:00;11:30\n", encoding="utf-8")
    lb = tmp_path / "2025W" / "lv_logbuch.xlsx"
    wb = openpyxl.load_workbook(lb)
    ws = wb["SOURCE"]
    for tag in (7, 14):
        ws.append([dt.datetime(2025, 10, tag, 18), "Zweit", "Zora", "zora@x.at", 12999001, 12999001, "Zweite LV [BBB]",
                   "Anwesenheits- und Mitarbeits-Vermerk", dt.datetime(2025, 10, tag), dt.datetime(2025, 10, tag), 1, 1])
    wb.save(lb)
    C.create_course(b, profil="VIAVO_v4.1", logbuch_lv=["BBB"])
    ea = lauf_bis_fertig(a)
    # Fragen zum gemeinsamen Logbuch (Eintragsart) sind in LV_A beantwortet und gelten auch für LV_B
    eb = run(b, C.decisions_for(b), STICHTAG)
    assert not [q for q in eb.offen if "Eintragsart" in q["frage"]]
    assert len(ea.students) == 8 and len(eb.students) == 2
    zora = next(s for s in eb.students if s.vorname == "Zora")
    zeno = next(s for s in eb.students if s.vorname == "Zeno")
    assert eb.ergebnisse[zora.key].anwesend == 2 and eb.ergebnisse[zora.key].mitarbeit == 2
    assert eb.ergebnisse[zeno.key].anwesend == 0
    assert all(zora.key not in d for d in (ea.anw, ea.mia))


def test_verbundene_lv_gemeinsamer_fehlterminzaehler(tmp_path):
    """VO und UE verbunden: Termine beider Teile zählen in einen Zähler."""
    vo = erzeuge(tmp_path, kurs="WIA_VO")
    ue = tmp_path / "2025W" / "WIA_UE"
    ue.mkdir()
    (ue / "termine.csv").write_text("DATUM;VON;BIS\n07.10.2025;10:00;11:30\n14.10.2025;10:00;11:30\n", encoding="utf-8")
    C.create_course(ue, profil="BEISPIEL", logbuch_lv=["BSP"])
    cfg = C.load_course(vo)
    cfg["verbunden_mit"] = ["../WIA_UE"]
    C.save_course(vo, cfg)
    erg = lauf_bis_fertig(vo)
    assert len(erg.termine) == 12                     # 10 VO + 2 UE
    assert all(r.abwesend >= 2 for r in erg.ergebnisse.values())   # die UE-Termine hat niemand gemeldet


def test_profil_anpassen_und_im_naechsten_semester_wiederverwenden(kurs, tmp_path, monkeypatch):
    from lvtool import app
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    app.CACHE.clear()
    app.aktion("wurzel", {"pfad": str(tmp_path)})
    p = app.aktion("profil_laden", {"id": "BEISPIEL"})["profil"]
    p["id"] = "MEINE_LV_2025W"
    p["teilleistungen"].append({"id": "REFERAT", "name": "Referat", "typ": "liste", "max": 15, "spalten": ["MAIN:REFERAT"], "positiv": 8})
    p["notenschluessel"][0]["unter"] = 55
    app.aktion("profil_speichern", {"profil": p, "neu": True})
    app.aktion("kurs_speichern", {"pfad": str(kurs), "cfg": {"profil": "MEINE_LV_2025W"}})
    app.aktion("semester_anlegen", {"name": "2026S"})
    app.aktion("kurs_anlegen", {"semester": "2026S", "name": "BSP_LV", "von": str(kurs)})
    neu = tmp_path / "2026S" / "BSP_LV"
    pid = C.load_course(neu)["profil"]
    q = app.aktion("profil_laden", {"id": pid})["profil"]
    assert pid == "BSP_LV_2026S"
    assert [t["id"] for t in q["teilleistungen"]][-1] == "REFERAT" and q["notenschluessel"][0]["unter"] == 55
    # Änderung im neuen Semester lässt das alte Profil unberührt
    q["teilleistungen"] = q["teilleistungen"][:2]
    app.aktion("profil_speichern", {"profil": q})
    assert len(app.aktion("profil_laden", {"id": "MEINE_LV_2025W"})["profil"]["teilleistungen"]) == 5


def test_hochladen(kurs, tmp_path, monkeypatch):
    from lvtool import app
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    app.aktion("wurzel", {"pfad": str(tmp_path)})
    r = app.hochladen(str(kurs), "semester", "C:\\x\\neu.csv", b"a;b\n1;2\n", False)
    assert r["status"] == "gespeichert" and (kurs.parent / "neu.csv").exists()
    assert app.hochladen(str(kurs), "semester", "neu.csv", b"a;b\n1;2\n", False)["status"] == "unveraendert"
    assert app.hochladen(str(kurs), "semester", "neu.csv", b"anders", False)["status"] == "vorhanden"
    assert app.hochladen(str(kurs), "semester", "neu.csv", b"anders", True)["status"] == "gespeichert"
    with pytest.raises(ValueError):
        app.hochladen(str(kurs), "kurs", "../../boese.exe", b"x", False)


def _tn_datei(pfad, studenten_):
    from lvtool.beispiel import TN_KOPF, _spreadsheetml
    rows = [TN_KOPF] + [["Standardgruppe", i + 1, "fix", s["nachname"], s["vorname"], "N", s["matrikel"], "UA", f"S{i}",
                         "Studium", 3, "2025-09-01", "", s["email"], ""] for i, s in enumerate(studenten_)]
    pfad.write_text(_spreadsheetml(rows), encoding="utf-8")


def test_zugaenge_abgaenge_und_ablage(kurs, tmp_path):
    """Neue Teilnehmerliste dazulegen: Neue kommen herein, Abgänger wandern auf Wunsch in die Ablage."""
    import os
    import time

    from lvtool.export_excel import write_master
    erg = lauf_bis_fertig(kurs)
    assert len(erg.aktive) == 8
    alle = studenten()
    geht = alle[5]
    neu = {"nachname": "Nachzügler", "vorname": "Nora", "matrikel": "12777001", "email": "nora@x.at", "code": 9}
    datei = kurs / "TN_LV999999_neu.xml"
    _tn_datei(datei, [s for s in alle if s is not geht] + [neu])
    jetzt = time.time() + 5
    os.utime(datei, (jetzt, jetzt))                       # neuere Fassung
    dec = C.decisions_for(kurs)
    erg = run(kurs, dec, STICHTAG)
    fragen = [q for q in erg.offen if "nicht mehr auf der aktuellen Teilnehmerliste" in q["frage"]]
    assert len(fragen) == 1 and geht["nachname"] in fragen[0]["frage"]
    assert any("Nachzügler" in n for n in erg.neu_studenten)
    assert not [q for q in erg.offen if "Wofür ist die Tabelle" in q["frage"]], "zweite Liste darf keine Rollenfrage auslösen"
    dec.answer(fragen[0]["id"], "ablage")
    for _ in range(5):
        erg = run(kurs, dec, STICHTAG)
        if not erg.offen:
            break
        beantworte(dec, erg, standard)
    assert len(erg.aktive) == 8 and [s.nachname for s in erg.abgelegte] == [geht["nachname"]]
    # Daten des Abgängers bleiben dokumentiert
    r = erg.ergebnisse[erg.abgelegte[0].key]
    assert r.anwesend > 0 and r.teile["HUE_50"].wert is not None
    ziel = write_master(erg, tmp_path / "m.xlsx")
    tabs = {t.sheet: t for t in read_tables(ziel)}
    assert len(tabs["Ablage"].rows) == 1 and len(tabs["Master"].rows) == 8
    assert geht["nachname"] not in [m["student"].nachname for m in R.entwuerfe(erg)]
    # zurückholen
    dec.answer(qid("ablage", erg.abgelegte[0].key), "nein")
    assert len(run(kurs, dec, STICHTAG).aktive) == 9


def test_ablage_stellt_keine_fragen(kurs):
    erg = lauf_bis_fertig(kurs)
    dec = C.decisions_for(kurs)
    s1 = next(s for s in erg.students if s.nachname == "Beispiel")   # hat die „teilweise“-Meldung
    for k in [k for k, v in dec.answers.items() if "Beispiel" in v.get("frage", "")]:
        dec.answers.pop(k)
    assert run(kurs, dec, STICHTAG).offen
    dec.answer(qid("ablage", s1.key), "ja")
    assert not run(kurs, dec, STICHTAG).offen


def test_manuelle_punkte_und_faktor(kurs, tmp_path):
    root = tmp_path
    p = C.load_profile(root, "BEISPIEL")
    p["id"] = "MAN"
    for t in p["teilleistungen"]:
        if t["id"] == "TEST_30":
            t["faktor"] = 0.5
            t["max"] = 15
    p["teilleistungen"].append({"id": "BONUS", "name": "Bonus/Malus", "typ": "manuell", "max": None, "zusatz": True})
    import json
    (root / "_profile" / "MAN.json").write_text(json.dumps(p), encoding="utf-8")
    cfg = C.load_course(kurs)
    cfg["profil"] = "MAN"
    C.save_course(kurs, cfg)
    erg = lauf_bis_fertig(kurs)
    s = erg.students[0]
    r = erg.ergebnisse[s.key]
    roh = {x["matrikel"]: i for i, x in enumerate(studenten())}
    assert r.teile["TEST_30"].wert == (10 + 2 * roh[s.matrikel]) * 0.5
    assert r.teile["BONUS"].wert is None and r.teile["BONUS"].status == "ok"
    dec = C.decisions_for(kurs)
    dec.answer(qid("manuell", s.key, "BONUS"), "-2.5")
    r2 = run(kurs, dec, STICHTAG).ergebnisse[s.key]
    assert r2.teile["BONUS"].wert == -2.5 and r2.summe == r.summe - 2.5


def test_altes_xls_format(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Liste")
    for c, h in enumerate(["Familienname", "Vorname", "Matrikelnummer", "E-Mail"]):
        ws.write(0, c, h)
    for c, v in enumerate(["Alt", "Anton", 12345678, "a@x.at"]):
        ws.write(1, c, v)
    wb.save(str(tmp_path / "alt.xls"))
    t = read_tables(tmp_path / "alt.xls")[0]
    assert t.headers[:3] == ["Familienname", "Vorname", "Matrikelnummer"] and t.rows[0]["Matrikelnummer"] == 12345678


def test_entwurf_bearbeiten_und_semester_abschliessen(kurs, tmp_path, monkeypatch):
    from lvtool import app
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    app.CACHE.clear()
    app.aktion("wurzel", {"pfad": str(tmp_path)})
    lauf_bis_fertig(kurs)
    e = app.aktion("verarbeiten", {"pfad": str(kurs)})
    key = e["studenten"][0]["key"]
    orig = app.aktion("entwurf", {"pfad": str(kurs), "key": key})["html"]
    app.aktion("entwurf_speichern", {"pfad": str(kurs), "key": key, "html": orig.replace("Hallo,", "Liebe Anna,")})
    r = app.aktion("entwurf", {"pfad": str(kurs), "key": key})
    assert r["bearbeitet"] and "Liebe Anna," in r["html"]
    app.aktion("entwuerfe", {"pfad": str(kurs), "art": "datei"})
    emls = list((kurs / "_lvtool" / "ausgabe" / "entwuerfe").glob("*.eml"))
    assert len(emls) == 8 and sum(b"Liebe Anna," in p.read_bytes() for p in emls) == 1
    # Daten ändern sich -> Bearbeitung gilt als veraltet und wird nicht stillschweigend verwendet
    app.aktion("status", {"pfad": str(kurs), "key": key, "wert": "nachfrist"})
    r = app.aktion("entwurf", {"pfad": str(kurs), "key": key})
    assert r["veraltet"] and not r["bearbeitet"] and "Liebe Anna," not in r["html"]

    sem = kurs.parent
    originale = {p: p.read_bytes() for p in sem.rglob("*") if p.is_file() and "_lvtool" not in p.parts}
    a = app.aktion("semester_archivieren", {"pfad": str(sem), "ziel": "", "originale": False})
    import zipfile
    namen = zipfile.ZipFile(a["datei"]).namelist()
    assert any(n.endswith("masterliste.xlsx") for n in namen) and not any(n.endswith("punkte.xlsx") for n in namen)
    app.aktion("semester_abschliessen", {"pfad": str(sem), "loeschen": True})
    assert not (kurs / "_lvtool" / "ausgabe").exists()
    assert all(v["kategorie"] == "struktur" for v in C.decisions_for(kurs).answers.values())
    assert all(p.read_bytes() == b for p, b in originale.items()), "Originaldateien müssen unverändert bleiben"
    with pytest.raises(ValueError):
        app.aktion("verarbeiten", {"pfad": str(kurs)})
    assert app.ueberwache_einmal() == []            # abgeschlossene Semester werden nicht mehr verarbeitet
    app.aktion("semester_oeffnen", {"pfad": str(sem)})
    assert app.aktion("verarbeiten", {"pfad": str(kurs)})["studenten"]


def test_ueberwachung_aller_lv(tmp_path, monkeypatch):
    from lvtool import app
    monkeypatch.setenv("APPDATA", str(tmp_path / "_appdata"))
    app.CACHE.clear()
    app.STAENDE.clear()
    a = erzeuge(tmp_path, kurs="LV_A")
    b = erzeuge(tmp_path, kurs="LV_B")
    app.aktion("wurzel", {"pfad": str(tmp_path)})
    assert sorted(app.ueberwache_einmal()) == sorted([str(a), str(b)])
    assert app.ueberwache_einmal() == []
    z = app.zustand()
    assert all(k["offen"] for k in z["semester"][0]["kurse"])     # Zähler offener Fragen je LV
    import os
    import time
    t = time.time() + 5
    os.utime(b / "punkte.xlsx", (t, t))
    assert app.ueberwache_einmal() == [str(b)]
