"""Kommandozeile: python -m lvtool <befehl> ..."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from . import course as C
from .decisions import PERSON, qid
from .pipeline import STATUS_WERTE, run


def _out(*a):
    print(*a, flush=True)


def frage_interaktiv(dec, q: dict) -> str | None:
    """Gibt 'stop' zurück, wenn die Benutzerin abbrechen will."""
    _out("\n" + q["frage"])
    for i, (_, lbl) in enumerate(q["optionen"], start=1):
        _out(f"  {i}) {lbl}")
    hinweis = "Nummer" + (" oder eigene Zahl" if q["freitext"] else "") + ", Enter = überspringen, x = beenden: "
    while True:
        try:
            e = input(hinweis).strip()
        except EOFError:
            return "stop"
        if e == "":
            return None
        if e.lower() == "x":
            return "stop"
        if e.isdigit() and 1 <= int(e) <= len(q["optionen"]) and not (q["freitext"] and int(e) > len(q["optionen"])):
            wert = q["optionen"][int(e) - 1][0]
            break
        if q["freitext"]:
            wert = e
            break
        _out("Bitte eine der Nummern eingeben.")
    dec.answer(q["id"], wert)
    dec.save()
    return None


def verarbeiten(kurs: Path, interaktiv: bool, stichtag: date | None):
    from .export_excel import write_master
    dec = C.decisions_for(kurs)
    for runde in range(1, 30):
        erg = run(kurs, dec, stichtag)
        if not erg.offen or not interaktiv:
            break
        _out(f"\n— {len(erg.offen)} Klärungsfragen (Runde {runde}) —")
        stop = False
        beantwortet = 0
        for q in erg.offen:
            if frage_interaktiv(dec, q) == "stop":
                stop = True
                break
            if q["id"] in dec.answers:
                beantwortet += 1
        if stop or beantwortet == 0:
            erg = run(kurs, dec, stichtag)
            break
    dec.save()
    ziel = write_master(erg, kurs / C.APPDIR / "ausgabe" / "masterliste.xlsx")
    _out(f"\nMasterliste: {ziel}")
    _out(f"Studenten: {len(erg.students)}, Termine bis Stichtag: {len(erg.termine)}, später: {len(erg.termine_spaeter)}")
    if erg.kontrolle:
        _out(f"Rechenkontrolle: {len(erg.kontrolle)} Abweichungen zu den Dateien (Blatt „Rechenkontrolle“).")
    if erg.warnungen:
        _out(f"Warnungen: {len(erg.warnungen)} (Blatt „Warnungen“).")
    _out(f"Offene Fragen: {len(erg.offen)}" + (" – bitte „klaeren“ ausführen." if erg.offen else ""))
    return erg


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="lvtool", description="Studenten- und Leistungsdaten einer Lehrveranstaltung")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("einrichten", help="Eingangsordner mit Profilbibliothek und Vorlagen anlegen")
    a.add_argument("wurzel", type=Path)

    a = sub.add_parser("kurs-anlegen", help="Lehrveranstaltung anlegen, optional als Kopie des Vorsemesters")
    a.add_argument("kurs", type=Path)
    a.add_argument("--von", type=Path)
    a.add_argument("--profil")
    a.add_argument("--logbuch-lv", nargs="*")
    a.add_argument("--lv-titel")
    a.add_argument("--vorlage")

    a = sub.add_parser("profile", help="verfügbare Bewertungsprofile")
    a.add_argument("wurzel", type=Path)
    a = sub.add_parser("profil-kopieren")
    a.add_argument("wurzel", type=Path)
    a.add_argument("alt")
    a.add_argument("neu")

    for name in ("verarbeiten", "klaeren"):
        a = sub.add_parser(name, help="Daten verarbeiten und Masterliste erstellen" if name == "verarbeiten"
                           else "offene Fragen beantworten")
        a.add_argument("kurs", type=Path)
        a.add_argument("--stichtag", type=date.fromisoformat)
        a.add_argument("--nicht-interaktiv", action="store_true")

    a = sub.add_parser("status", help="Status eines Studenten setzen")
    a.add_argument("kurs", type=Path)
    a.add_argument("matrikel")
    a.add_argument("wert", choices=list(STATUS_WERTE) + ["normal"])

    a = sub.add_parser("entwuerfe", help="E-Mail-Entwürfe erstellen (versendet nichts)")
    a.add_argument("kurs", type=Path)
    a.add_argument("--outlook", action="store_true", help="Entwürfe direkt im klassischen Outlook anlegen")
    a.add_argument("--konto", default="", help="SMTP-Adresse des Outlook-Kontos (sonst Standardkonto)")
    a.add_argument("--trotzdem", action="store_true", help="auch bei offenen Fragen")
    a.add_argument("--stichtag", type=date.fromisoformat)

    a = sub.add_parser("beobachten", help="Eingangsordner überwachen und bei Änderungen neu verarbeiten")
    a.add_argument("wurzel", type=Path)
    a.add_argument("--intervall", type=int, default=60)

    a = sub.add_parser("vorlage", help="Standard-Report-Vorlage mit Platzhaltern erzeugen")
    a.add_argument("ziel", type=Path)

    ns = ap.parse_args(argv)

    if ns.cmd == "einrichten":
        C.init_root(ns.wurzel)
        from .report import erstelle_standard_vorlage
        v = ns.wurzel / C.VORLAGENDIR / "report_standard.docx"
        if not v.exists():
            erstelle_standard_vorlage(v)
        _out(f"Eingerichtet: {ns.wurzel}")
    elif ns.cmd == "kurs-anlegen":
        cfg = C.create_course(ns.kurs, von=ns.von, profil=ns.profil, logbuch_lv=ns.logbuch_lv,
                              lv_titel=ns.lv_titel, vorlage=ns.vorlage)
        _out(f"Angelegt: {ns.kurs}\n{json.dumps(cfg, ensure_ascii=False, indent=2)}")
    elif ns.cmd == "profile":
        for p in C.list_profiles(ns.wurzel):
            prof = C.load_profile(ns.wurzel, p)
            _out(f"{p}: {prof.get('bezeichnung', '')}")
            for z in prof.get("zu_bestaetigen", []):
                _out(f"    zu bestätigen: {z}")
    elif ns.cmd == "profil-kopieren":
        _out(f"Kopiert nach {C.copy_profile(ns.wurzel, ns.alt, ns.neu)}")
    elif ns.cmd in ("verarbeiten", "klaeren"):
        verarbeiten(ns.kurs.resolve(), not ns.nicht_interaktiv, ns.stichtag)
    elif ns.cmd == "status":
        from .matching import norm_matrikel
        dec = C.decisions_for(ns.kurs)
        key = norm_matrikel(ns.matrikel)
        dec.answer(qid("status", key), ns.wert, frage=f"Status für Matrikelnummer {ns.matrikel}", kategorie=PERSON)
        dec.save()
        _out(f"Status {ns.wert} für {ns.matrikel} gespeichert.")
    elif ns.cmd == "entwuerfe":
        from . import report as R
        kurs = ns.kurs.resolve()
        erg = run(kurs, stichtag=ns.stichtag)
        if erg.offen and not ns.trotzdem:
            _out(f"Es gibt {len(erg.offen)} offene Fragen. Bitte zuerst „klaeren“ ausführen (oder --trotzdem).")
            return 2
        mails = R.entwuerfe(erg)
        ziel = kurs / C.APPDIR / "ausgabe"
        ohne = [m["student"].anzeige for m in mails if not m["an"]]
        emls = R.schreibe_eml(mails, ziel / "entwuerfe", erg.cfg.get("absender_email", ""))
        x, c = R.schreibe_serienbrief(mails, ziel)
        _out(f"{len(emls)} Entwürfe (.eml) in {ziel / 'entwuerfe'}\nSerienbrief: {x} und {c}")
        if ohne:
            _out("Ohne E-Mail-Adresse: " + "; ".join(ohne))
        if ns.outlook:
            n = R.outlook_entwuerfe([m for m in mails if m["an"]], ns.konto or erg.cfg.get("absender_email", ""))
            _out(f"{n} Entwürfe im Outlook-Ordner „Entwürfe“ angelegt. Es wurde nichts versendet.")
    elif ns.cmd == "beobachten":
        stand: dict = {}
        _out(f"Beobachte {ns.wurzel} (Strg+C beendet).")
        while True:
            for kj in sorted(ns.wurzel.glob(f"*/*/{C.APPDIR}/kurs.json")):
                kurs = kj.parent.parent
                mt = max([p.stat().st_mtime for base in (kurs, kurs.parent) for p in base.iterdir() if p.is_file()]
                         + [kj.stat().st_mtime, (kurs / C.APPDIR / "entscheidungen.json").stat().st_mtime
                            if (kurs / C.APPDIR / "entscheidungen.json").exists() else 0])
                if stand.get(kurs) != mt:
                    stand[kurs] = mt
                    _out(f"\n[{time.strftime('%Y-%m-%d %H:%M')}] Änderung in {kurs.parent.name}/{kurs.name}")
                    try:
                        verarbeiten(kurs, False, None)
                    except Exception as e:  # noqa: BLE001
                        _out(f"Fehler: {e}")
            time.sleep(ns.intervall)
    elif ns.cmd == "vorlage":
        from .report import erstelle_standard_vorlage
        _out(f"Vorlage: {erstelle_standard_vorlage(ns.ziel)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
