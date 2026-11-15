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
    assert C.load_course(neu_dir)["profil"] == "BEISPIEL"


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


def test_jat_vorlage(tmp_path):
    v = R.erstelle_jat_vorlage(tmp_path / "jat.docx")
    ph = R.platzhalter_der_vorlage(v)
    assert {"TEST_30", "HUE_1_40", "AW_SCORE_10", "MIA_ALL_10", "RECH_10", "EXTRA_SCORE", "KOMMENTAR"} <= ph
