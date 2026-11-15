"""Verarbeitungskette einer Lehrveranstaltung: Import, Abgleich, Anwesenheit, Punkte, Note."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import course as C
from .decisions import LISTE, PERSON, STRUKTUR, TERMIN, Decisions, qid
from .io_tables import SUPPORTED, Table, TableError, parse_date, parse_date_candidates, parse_number, read_tables
from .mapping import assign_roles, find_alias_column, resolve_fields
from .matching import Student, StudentIndex, norm_matrikel, norm_text

STATUS_WERTE = {
    "ausgestiegen": "ausgestiegen",
    "unbeurteilt": "unbeurteilt",
    "vorlaeufig": "vorläufig",
    "nachfrist": "vorläufig (Nachfrist)",
    "endgueltig": "endgültig",
}

HINWEIS_RX = re.compile(r"entschuld|krank|versp|sp[aä]t|fr[uü]her|vorzeitig|gegangen|attest|nachweis|"
                        r"kompens|nicht da|abwesend|teilweise|verl[aä]ss", re.I)


@dataclass
class Termin:
    datum: date
    von: str = ""
    bis: str = ""
    kurs: str = ""

    @property
    def label(self) -> str:
        return self.datum.isoformat()


@dataclass
class Eintrag:
    anw: bool | None
    mia: int
    sonder: bool
    quelle: str


@dataclass
class Teilergebnis:
    id: str
    name: str
    max: float | None
    wert: float | None = None
    status: str = "ok"            # ok | fehlt | offen | vorläufig
    positiv: bool | None = None
    zusatz: bool = False
    quelle: str = ""


@dataclass
class StudentErgebnis:
    student: Student
    teile: dict[str, Teilergebnis] = field(default_factory=dict)
    summe: float = 0.0
    note: str = ""
    status: str = ""
    hinweise: list[str] = field(default_factory=list)
    anwesend: int = 0
    abwesend: int = 0
    mitarbeit: int = 0
    pflicht: str = ""             # erfüllt | nicht erfüllt | offen
    texte: dict[str, str] = field(default_factory=dict)


@dataclass
class Ergebnis:
    kurs_dir: Path
    cfg: dict
    profil: dict
    dec: Decisions
    students: list[Student] = field(default_factory=list)
    termine: list[Termin] = field(default_factory=list)
    termine_spaeter: list[Termin] = field(default_factory=list)
    anw: dict = field(default_factory=dict)      # key -> {datum: "A"|"S"|""}
    mia: dict = field(default_factory=dict)      # key -> {datum: int}
    sonder: dict = field(default_factory=dict)   # key -> [Beschreibung]
    kompensation: dict = field(default_factory=dict)
    ergebnisse: dict[str, StudentErgebnis] = field(default_factory=dict)
    protokoll: list = field(default_factory=list)
    kontrolle: list = field(default_factory=list)
    warnungen: list = field(default_factory=list)
    neu_studenten: list = field(default_factory=list)   # seit dem letzten Lauf hinzugekommen
    bereinigung: dict = field(default_factory=dict)     # Zähler der automatischen Bereinigung

    @property
    def aktive(self) -> list[Student]:
        return [s for s in self.students if not s.abgelegt]

    @property
    def abgelegte(self) -> list[Student]:
        return [s for s in self.students if s.abgelegt]

    @property
    def offen(self):
        return self.dec.open_list()


def fmt(x) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        x = round(x, 2)
        if x.is_integer():
            x = int(x)
    return str(x).replace(".", ",")


# --------------------------------------------------------------------------- Dateien

def _files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED
                  and not p.name.startswith(("~$", ".")))


def load_tables(kurs_dir: Path, erg: Ergebnis) -> list[Table]:
    tables = []
    for base, prefix in ((kurs_dir, ""), (C.semester_of(kurs_dir), "../")):
        for f in _files(base):
            try:
                neu = read_tables(f, prefix + f.name)
                for t in neu:
                    t.mtime = f.stat().st_mtime
                tables += neu
            except TableError as e:
                erg.warnungen.append(str(e))
    return tables


# --------------------------------------------------------------------------- Studenten

def load_students(tabs: list[Table], erg: Ergebnis) -> list[Student]:
    """Liest alle Fassungen der offiziellen Liste. Die neueste Datei ist maßgeblich; wer nur in einer
    älteren Fassung steht, bleibt erhalten und wird als „nicht mehr auf der aktuellen Liste“ markiert."""
    dec, out, seen = erg.dec, [], {}
    tabs = sorted(tabs, key=lambda t: (-t.mtime, t.source))
    fertig = 0
    for t in tabs:
        f = resolve_fields(t, "studenten", dec)
        if not f:
            continue
        fertig += 1
        platz_werte = {r.get(f["platz"]) for r in t.rows} if "platz" in f else set()
        in_tabelle = {}
        for row, n in zip(t.rows, t.row_numbers):
            raw = row.get(f.get("matrikel"))
            key = norm_matrikel(raw)
            if not key:
                erg.warnungen.append(f"{t.label} Zeile {n}: keine gültige Matrikelnummer – Zeile nicht übernommen.")
                continue
            if len(platz_werte) > 1:
                pv = row.get(f["platz"])
                a = dec.ask(qid("platz", pv), STRUKTUR,
                            f"Studentenliste: Sollen Einträge mit Platz „{pv}“ einbezogen werden?",
                            [("ja", "ja, einbeziehen"), ("nein", "nein, nicht einbeziehen")])
                if a != "ja":
                    continue
            name = (row.get(f.get("nachname")), row.get(f.get("vorname")))
            if key in in_tabelle:
                if in_tabelle[key] != name:
                    erg.warnungen.append(f"Matrikelnummer {raw} mehrfach mit unterschiedlichen Namen ({t.label}).")
                continue
            in_tabelle[key] = name
            if key in seen:
                continue                      # neuere Fassung hat Vorrang
            seen[key] = name
            s = Student(key=key, matrikel=str(raw).strip(), nachname=name[0] or "", vorname=name[1] or "",
                        email=row.get(f.get("email")), felder={k: row.get(v) for k, v in f.items()})
            if isinstance(raw, (int, float)) and len(str(int(raw))) == 7:
                s.matrikel = "0" + str(int(raw))
            s.nicht_aktuell = fertig > 1
            if s.nicht_aktuell:
                erg.protokoll.append(("Studentenliste", s.anzeige, f"nur noch in älterer Fassung {t.label}"))
            if not s.email:
                erg.warnungen.append(f"{s.anzeige}: keine E-Mail-Adresse in der offiziellen Liste.")
            out.append(s)
    if len(tabs) > 1:
        erg.protokoll.append(("Studentenliste", tabs[0].label, f"neueste von {len(tabs)} Fassungen; maßgeblich"))
    if not tabs:
        erg.warnungen.append("Keine offizielle Studentenliste gefunden.")
    for s in out:
        if s.nicht_aktuell:
            dec.ask(qid("abgang", s.key), LISTE,
                    f"{s.anzeige} steht nicht mehr auf der aktuellen Teilnehmerliste. Was soll geschehen?",
                    [("ablage", "in die Ablage verschieben (alle Daten bleiben dokumentiert)"),
                     ("behalten", "weiter in der Lehrveranstaltung mitführen")])
        ab = dec.get(qid("ablage", s.key))
        s.abgelegt = ab == "ja" or (ab != "nein" and s.nicht_aktuell and dec.get(qid("abgang", s.key)) == "ablage")
    return sorted(out, key=lambda s: (norm_text(s.nachname), norm_text(s.vorname)))


# --------------------------------------------------------------------------- Termine

def load_termine(tabs: list[Table], cfg: dict, dec: Decisions, erg: Ergebnis, kursname: str) -> list[Termin]:
    out: dict[date, Termin] = {}
    for t in tabs:
        f = resolve_fields(t, "termine", dec)
        if not f:
            continue
        lv_werte = {str(r.get(f["lv"])) for r in t.rows if r.get(f.get("lv"))} if "lv" in f else set()
        filt = [norm_text(x) for x in cfg.get("termin_lv") or []]
        for row, n in zip(t.rows, t.row_numbers):
            if "lv" in f and (filt or len(lv_werte) > 1):
                lv = str(row.get(f["lv"]) or "")
                if filt:
                    if norm_text(lv) not in filt:
                        continue
                else:
                    a = dec.ask(qid("termin_lv", kursname, lv), STRUKTUR,
                                f"Terminliste: Gehören Termine der LV „{lv}“ zu {kursname}?",
                                [("ja", "ja"), ("nein", "nein")])
                    if a != "ja":
                        continue
            d = parse_date(row.get(f["datum"]))
            if not d:
                erg.warnungen.append(f"{t.label} Zeile {n}: Datum nicht lesbar – Termin nicht übernommen.")
                continue
            anm = row.get(f.get("anmerkung")) if "anmerkung" in f else None
            if anm:
                a = dec.ask(qid("termin_anm", kursname, d.isoformat(), anm), TERMIN,
                            f"Termin {d.isoformat()} {row.get(f.get('von')) or ''} hat die Anmerkung „{anm}“. "
                            f"Zählt er als Lehrveranstaltungstermin für Anwesenheit und Mitarbeit?",
                            [("ja", "ja, zählt"), ("nein", "nein, nicht zählen")])
                if a != "ja":
                    erg.protokoll.append(("Termin", d.isoformat(), "nicht gezählt" if a else "offen"))
                    continue
            if d in out:
                erg.protokoll.append(("Termin", d.isoformat(), "mehrere Einheiten am selben Tag zusammengefasst"))
                continue
            out[d] = Termin(d, str(row.get(f.get("von")) or ""), str(row.get(f.get("bis")) or ""), kursname)
    return sorted(out.values(), key=lambda x: x.datum)


# --------------------------------------------------------------------------- Logbuch

LV_CODE_RX = re.compile(r"\[([^\]]+)\]")


def _lv_passt(wert, cfg: dict, dec: Decisions, kursname: str) -> bool:
    if wert is None:
        return True
    w = str(wert)
    codes = [norm_text(x) for x in cfg.get("logbuch_lv") or []]
    if codes:
        m = LV_CODE_RX.search(w)
        kandidaten = {norm_text(w)} | ({norm_text(m.group(1))} if m else set())
        return bool(kandidaten & set(codes))
    a = dec.ask(qid("logbuch_lv", kursname, w), STRUKTUR,
                f"Logbuch: Gehören Einträge zur LV „{w}“ zur Lehrveranstaltung {kursname}?",
                [("ja", "ja"), ("nein", "nein")])
    return a == "ja"


def _art(wert, dec: Decisions) -> str | None:
    if wert is None:
        return "vermerk"
    w = str(wert)
    vorschlag = "vermerk" if re.search(r"anwesen|mitarbeit", w, re.I) else "sonstiges"
    opts = [("vermerk", "Anwesenheits-/Mitarbeitsvermerk"), ("sonstiges", "sonstiger Eintrag, jeweils einzeln vorlegen"),
            ("ignorieren", "nicht werten")]
    opts.sort(key=lambda o: o[0] != vorschlag)
    opts[0] = (opts[0][0], opts[0][1] + " (Vorschlag)")
    return dec.ask(qid("logbuch_art", w), STRUKTUR, f"Logbuch: Was bedeutet die Eintragsart „{w}“?", opts,
                   semesterweit=True)


def _datum(row, f, termine: set, dec, quelle, ident, eid=None, erg=None, idx=None) -> date | None:
    """Ermittelt den gemeinten Termin. Eindeutige Korrekturen (Wiederholungsfeld, Tag/Monat vertauscht,
    falsches Jahr, knapp daneben bei nur einem möglichen Termin) erfolgen automatisch und werden protokolliert."""
    roh = [row.get(f[k]) for k in ("datum", "datum2") if k in f and row.get(f[k]) is not None]
    cands = []
    for r in roh:
        for c in parse_date_candidates(r):
            if c not in cands:
                cands.append(c)
    treffer = [c for c in cands if c in termine]

    def korrigiert(d, wie):
        if erg is not None:
            erg.protokoll.append(("Datum bereinigt", quelle, f"{ident}: {' / '.join(map(str, roh))} → {d.isoformat()} ({wie})"))
        if idx is not None:
            idx.statistik["datum"] += 1
        return d

    if len(treffer) == 1:
        if len({str(r) for r in roh}) > 1 or treffer[0] != cands[0]:
            return korrigiert(treffer[0], "nur diese Angabe ist ein Termin")
        return treffer[0]
    if not treffer and cands:
        varianten = set()
        for c in cands:
            if c.day <= 12:                                   # Tag und Monat vertauscht
                try:
                    varianten.add(date(c.year, c.day, c.month))
                except ValueError:
                    pass
            for t in termine:                                 # falsches Jahr
                if (t.month, t.day) == (c.month, c.day):
                    varianten.add(t)
        v = sorted(x for x in varianten if x in termine)
        if len(v) == 1:
            return korrigiert(v[0], "Tag/Monat vertauscht oder falsches Jahr")
        nahe = sorted({t for c in cands for t in termine if abs((t - c).days) <= 3})
        if len(nahe) == 1 and not v:
            return korrigiert(nahe[0], "einziger Termin in drei Tagen Abstand")
    nahe = sorted({t for c in cands for t in termine if abs((t - c).days) <= 3})
    opts = [(t.isoformat(), t.isoformat()) for t in (treffer or nahe)] + [("__ignorieren__", "Eintrag nicht werten")]
    if not (treffer or nahe):
        if erg is not None:   # weit von jedem Termin entfernt: kein Zweifelsfall, sondern kein Termin dieser LV
            erg.protokoll.append(("Kein Termin", quelle, f"{ident}: {' / '.join(map(str, roh)) or '(leer)'} – nicht gewertet"))
        return None
    grund = "passt zu mehreren Terminen" if len(treffer or nahe) > 1 else "passt zu keinem offiziellen Termin"
    a = dec.ask(qid("datum", eid or quelle, *map(str, roh)), PERSON,
                f"{quelle} ({ident}): Datumsangabe {' / '.join(map(str, roh)) or '(leer)'} {grund}. Welcher Termin ist gemeint?",
                opts)
    if a and a != "__ignorieren__":
        return date.fromisoformat(a)
    return None


JA = {"ja", "j", "yes", "y", "x", "anwesend", "wahr", "true"}
NEIN = {"nein", "n", "no", "nicht anwesend", "abwesend", "falsch", "false", "-"}


def _zahl_oder_wort(v):
    """Zahl aus Zahl, Zahl-als-Text oder Ja/Nein-Wort; None, wenn nicht eindeutig."""
    num = parse_number(v)
    if num is not None:
        return num
    w = norm_text(v)
    if w in JA:
        return 1.0
    if w in NEIN:
        return 0.0
    return None


def load_logbook(tabs, cfg, dec, idx: StudentIndex, termine: list[Termin], erg: Ergebnis, kursname: str):
    tset = {t.datum for t in termine}
    roh: dict[tuple, list[Eintrag]] = {}
    for t in tabs:
        f = resolve_fields(t, "logbuch", dec)
        if not f:
            continue
        for row, n in zip(t.rows, t.row_numbers):
            if "lv" in f and not _lv_passt(row.get(f["lv"]), cfg, dec, kursname):
                continue
            quelle = f"{t.label} Zeile {n}"
            s = idx.resolve(dec, erg.protokoll, quelle,
                            matrikel=[row.get(f[k]) for k in ("matrikel", "matrikel2") if k in f],
                            email=row.get(f.get("email")) if "email" in f else None,
                            nachname=row.get(f.get("nachname")) if "nachname" in f else None,
                            vorname=row.get(f.get("vorname")) if "vorname" in f else None)
            dec.stumm = bool(s and s.abgelegt)
            if not s:
                continue
            # stabile Kennung des Eintrags: bleibt gleich, wenn das Logbuch neu exportiert oder umbenannt wird
            zs = row.get(f["zeitstempel"]) if "zeitstempel" in f else None
            eid = f"{zs}|{s.key}" if zs else quelle
            art = _art(row.get(f["art"]), dec) if "art" in f else "vermerk"
            if art in (None, "ignorieren"):
                continue
            text = row.get(f["text"]) if "text" in f else None
            upload = row.get(f["upload"]) if "upload" in f else None
            d = _datum(row, f, tset, dec, quelle, s.anzeige, eid, erg, idx)
            if art == "sonstiges":
                a = dec.ask(qid("sonstiges", eid, s.key, text, upload), PERSON,
                            f"{quelle}: Sonstiger Eintrag von {s.anzeige}"
                            f"{' zum ' + d.isoformat() if d else ''}. Text: „{text or ''}“"
                            f"{'; Upload: ' + str(upload) if upload else ''}. Wie werten?",
                            [("kenntnis", "nur zur Kenntnis, nicht werten"),
                             ("anwesend", "als Anwesenheit am genannten Termin werten"),
                             ("kompensation", "als Begründung/Nachweis/Kompensation vermerken")])
                if a == "kompensation":
                    erg.kompensation.setdefault(s.key, []).append(f"{d.isoformat() if d else ''} {text or ''}".strip())
                if a == "anwesend" and d:
                    roh.setdefault((s.key, d), []).append(Eintrag(True, 0, False, quelle))
                continue
            if not d:
                continue
            # Anwesenheit
            av = row.get(f["anwesenheit"]) if "anwesenheit" in f else None
            sonder = False
            num = _zahl_oder_wort(av)
            if av is None:
                anw = None
            elif num is not None:
                anw = num >= 1
            else:
                a = dec.ask(qid("anw_wert", eid, av, text), PERSON,
                            f"{quelle}: {s.anzeige} gibt für {d.isoformat()} bei Anwesenheit „{av}“ an"
                            f"{'; Text: „' + str(text) + '“' if text else ''}. Wie werten?",
                            [("anwesend", "anwesend"), ("abwesend", "nicht anwesend"),
                             ("sonder", "anwesend, Verspätung bzw. vorzeitiges Verlassen vermerken")])
                if a is None:
                    continue
                anw, sonder = a in ("anwesend", "sonder"), a == "sonder"
            # Mitarbeit
            mv = row.get(f["mitarbeit"]) if "mitarbeit" in f else None
            mnum = _zahl_oder_wort(mv)
            if mv is None:
                mia = 0
            elif mnum is not None and mnum >= 0:
                mia = int(mnum)
            else:
                a = dec.ask(qid("mia_wert", eid, mv, text), PERSON,
                            f"{quelle}: {s.anzeige} gibt für {d.isoformat()} bei Mitarbeit „{mv}“ an"
                            f"{'; Text: „' + str(text) + '“' if text else ''}. Wie viele Mitarbeitseinträge zählen?",
                            [(str(i), str(i)) for i in range(0, 6)], freitext=True)
                if a is None:
                    continue
                mia = int(parse_number(a) or 0)
            # Hinweise im Freitext werden nicht gedeutet, sondern vorgelegt
            if text and HINWEIS_RX.search(str(text)):
                a = dec.ask(qid("text", eid, text), PERSON,
                            f"{quelle}: {s.anzeige}, {d.isoformat()}: Hinweis im Text „{text}“. Wie werten?",
                            [("normal", "wie angegeben werten"),
                             ("sonder", "wie angegeben, Verspätung bzw. vorzeitiges Verlassen vermerken"),
                             ("abwesend", "Anwesenheit nicht werten"),
                             ("ignorieren", "Eintrag nicht werten")])
                if a is None or a == "ignorieren":
                    continue
                if a == "abwesend":
                    anw = False
                if a == "sonder":
                    sonder = True
            roh.setdefault((s.key, d), []).append(Eintrag(anw, mia, sonder, quelle))
    dec.stumm = False
    _combine(roh, erg, dec)
    dec.stumm = False


def _combine(roh, erg: Ergebnis, dec: Decisions):
    prof = erg.profil
    maxpt = prof.get("mitarbeit", {}).get("max_pro_termin")
    nach_key = {s.key: s for s in erg.students}
    for (key, d), eintraege in roh.items():
        s_lbl = nach_key[key].anzeige if key in nach_key else key
        dec.stumm = bool(key in nach_key and nach_key[key].abgelegt)
        gleich = len({(e.anw, e.mia, e.sonder) for e in eintraege}) == 1
        if gleich and len(eintraege) > 1 and eintraege[0].mia > 0:
            gleich = False
        if gleich:
            if len(eintraege) > 1:
                erg.protokoll.append(("Doppelmeldung", f"{s_lbl} {d}", f"{len(eintraege)} gleichlautende Einträge, einmal gezählt"))
            e = eintraege[0]
            anw, mia, sonder = e.anw, e.mia, e.sonder
        else:
            werte = [e.mia for e in eintraege]
            anw = True if any(e.anw for e in eintraege) else (False if any(e.anw is False for e in eintraege) else None)
            sonder = any(e.sonder for e in eintraege)
            if len(set(werte)) == 1 and werte[0] == 0:
                mia = 0
            else:
                a = dec.ask(qid("mehrfach", key, d.isoformat(), *werte), PERSON,
                            f"{s_lbl}, {d.isoformat()}: mehrere Einträge mit Mitarbeit {', '.join(map(str, werte))} "
                            f"({'; '.join(e.quelle for e in eintraege)}). Was zählt?",
                            [("max", f"höchster Wert ({max(werte)})"), ("summe", f"Summe ({sum(werte)})"),
                             ("erster", f"erster Eintrag ({werte[0]})")])
                if a is None:
                    continue
                mia = {"max": max(werte), "summe": sum(werte), "erster": werte[0]}[a]
        if mia > 0 and anw is not True:
            if anw is None:
                a = dec.ask(qid("mia_impliziert_anw"), STRUKTUR,
                            "Logbuch: Gilt ein Mitarbeitseintrag ohne Angabe zur Anwesenheit zugleich als Anwesenheit?",
                            [("ja", "ja"), ("nein", "nein, jeweils nachfragen")])
            else:
                a = "nein"
            if a == "ja":
                anw = True
            elif a == "nein":
                b = dec.ask(qid("mia_ohne_anw", key, d.isoformat()), PERSON,
                            f"{s_lbl}, {d.isoformat()}: Mitarbeit ({mia}) eingetragen, aber "
                            f"{'Abwesenheit angegeben' if anw is False else 'keine Anwesenheit'}. Wie werten?",
                            [("anwesend", "als anwesend werten, Mitarbeit zählt"),
                             ("ohne_mia", "abwesend, Mitarbeit nicht zählen"),
                             ("beides", "abwesend, Mitarbeit trotzdem zählen")])
                if b == "anwesend":
                    anw = True
                elif b == "ohne_mia":
                    mia = 0
                elif b is None:
                    continue
        if maxpt is not None and mia > maxpt:
            a = dec.ask(qid("mia_hoch", key, d.isoformat(), mia), PERSON,
                        f"{s_lbl}, {d.isoformat()}: {mia} Mitarbeitseinträge an einem Termin (vorgesehen höchstens {maxpt}).",
                        [("max", f"auf {maxpt} begrenzen"), ("alle", f"alle {mia} zählen")])
            if a is None:
                continue
            if a == "max":
                mia = maxpt
        if anw:
            erg.anw.setdefault(key, {})[d] = "S" if sonder else "A"
            if sonder:
                erg.sonder.setdefault(key, []).append(d.isoformat())
        if mia:
            erg.mia.setdefault(key, {})[d] = mia


# --------------------------------------------------------------------------- Anwesenheitspflicht

def pflicht_relevant(profil: dict) -> bool:
    return any(t["typ"] in ("anwesenheit", "anwesenheit_mitarbeit") or t.get("positiv") == "erfuellt"
               for t in profil["teilleistungen"])


def pflicht_status(erg: Ergebnis, s: Student) -> tuple[str, int]:
    regel = erg.profil.get("anwesenheit", {})
    frei = regel.get("frei_versaeumbar", 0)
    komp = regel.get("mit_kompensation", 0)
    wdh = regel.get("sonderfall_wiederholt_ab")
    besucht = erg.anw.get(s.key, {})
    fehl = [t.datum for t in erg.termine if t.datum not in besucht]
    n = len(fehl)
    if not pflicht_relevant(erg.profil):
        return ("erfüllt" if n <= frei else f"{n} Fehltermine (nicht bewertet)"), n
    if n <= frei:
        status = "erfüllt"
    elif n <= frei + komp:
        hinweis = "; ".join(erg.kompensation.get(s.key, [])) or "keine im Logbuch"
        a = erg.dec.ask(qid("kompensation", s.key, n), PERSON,
                        f"{s.anzeige}: {n} Fehltermine ({', '.join(d.isoformat() for d in fehl)}); frei versäumbar sind {frei}, "
                        f"ein weiterer nur mit Begründung, Nachweis und Kompensation. Vermerke: {hinweis}. Liegt das vor?",
                        [("ja", "ja, Anwesenheitspflicht erfüllt"), ("nein", "nein, nicht erfüllt"),
                         ("ausstehend", "noch ausstehend (vorläufig)")])
        status = {"ja": "erfüllt", "nein": "nicht erfüllt", "ausstehend": "ausstehend"}.get(a, "offen")
    else:
        status = "nicht erfüllt"
    sonder = erg.sonder.get(s.key, [])
    if wdh and len(sonder) >= wdh and status == "erfüllt":
        a = erg.dec.ask(qid("sonder_wdh", s.key, len(sonder)), PERSON,
                        f"{s.anzeige}: {len(sonder)}-mal Verspätung bzw. vorzeitiges Verlassen ({', '.join(sonder)}). "
                        f"Systematische Fälle gelten als Nichterfüllung. Wie entscheiden Sie?",
                        [("erfuellt", "Anwesenheitspflicht trotzdem erfüllt"), ("nicht", "nicht erfüllt")])
        status = {"erfuellt": "erfüllt", "nicht": "nicht erfüllt"}.get(a, "offen")
    return status, n


# --------------------------------------------------------------------------- Punkte

@dataclass
class PunkteDaten:
    werte: dict = field(default_factory=dict)     # (key, comp) -> [(wert, quelle)]
    kontrolle: dict = field(default_factory=dict)  # (key, alias) -> [(wert, quelle)]
    texte: dict = field(default_factory=dict)      # (key, name) -> [text]
    gefunden: set = field(default_factory=set)


def load_points(tabs, erg: Ergebnis, idx: StudentIndex) -> PunkteDaten:
    pd = PunkteDaten()
    prof, cfg, dec = erg.profil, erg.cfg, erg.dec
    kontroll_aliases = list(prof.get("kontrollspalten_gesamt", [])) + list(prof.get("kontrollspalten_note", []))
    for tl in prof["teilleistungen"]:
        kontroll_aliases += tl.get("kontrolle", [])
    prepared = []
    for t in tabs:
        f = resolve_fields(t, "punkte", dec)
        if f is not None:
            prepared.append((t, f))
    # Tabellen mit Matrikel/E-Mail/Name zuerst, damit Codenummern bekannt sind
    prepared.sort(key=lambda x: set(x[1]) <= {"codenummer"})
    for t, f in prepared:
        comps = {tl["id"]: find_alias_column(t, tl.get("spalten", []))
                 for tl in prof["teilleistungen"] if tl["typ"] == "liste"}
        comps = {k: v for k, v in comps.items() if v}
        ktr = {a: find_alias_column(t, [a]) for a in kontroll_aliases}
        ktr = {a: h for a, h in ktr.items() if h}
        txt = {name: find_alias_column(t, al) for name, al in (cfg.get("textfelder") or {}).items()}
        txt = {k: v for k, v in txt.items() if v}
        if not (comps or ktr or txt):
            erg.protokoll.append(("Punkteliste", t.label, "keine Spalten des Profils gefunden – nicht verwendet"))
            continue
        for row, n in zip(t.rows, t.row_numbers):
            relevant = [row.get(h) for h in list(comps.values()) + list(ktr.values()) + list(txt.values())]
            ident = {k: row.get(v) for k, v in f.items()}
            if all(v is None for v in ident.values()):
                continue
            s = idx.resolve(dec, erg.protokoll, f"{t.label} Zeile {n}",
                            matrikel=[ident.get("matrikel")] if ident.get("matrikel") else [],
                            email=ident.get("email"), nachname=ident.get("nachname"),
                            vorname=ident.get("vorname"),
                            codenummer=ident.get("codenummer") if not (set(f) - {"codenummer"}) else None)
            dec.stumm = bool(s and s.abgelegt)
            if not s:
                continue
            if ident.get("codenummer") and set(f) - {"codenummer"}:
                idx.register_code(s, ident["codenummer"])
            pd.gefunden.add(s.key)
            for cid, h in comps.items():
                v = row.get(h)
                if v is None:
                    continue
                num = parse_number(v)
                if num is None:
                    erg.warnungen.append(f"{t.label} Zeile {n}: {h} = „{v}“ ist keine Zahl.")
                    continue
                pd.werte.setdefault((s.key, cid), []).append((num, f"{t.label}: {h}"))
            for a, h in ktr.items():
                if row.get(h) is not None:
                    pd.kontrolle.setdefault((s.key, a), []).append((row.get(h), t.label))
            for name, h in txt.items():
                if row.get(h):
                    pd.texte.setdefault((s.key, name), []).append(str(row.get(h)))
            del relevant
    dec.stumm = False
    return pd


# --------------------------------------------------------------------------- Bewertung

def note_fuer(summe: float, schluessel: list[dict]) -> str:
    for g in schluessel:
        if g.get("unter") is None or summe < g["unter"]:
            return g["note"]
    return schluessel[-1]["note"]


def _positiv(tl: dict, wert: float, pflicht: str, anteil: float) -> bool:
    regel = tl.get("positiv", "anteil")
    if regel == "keine":
        return True
    if regel == "erfuellt":
        return pflicht == "erfüllt"
    if isinstance(regel, (int, float)):
        return wert >= regel
    return tl.get("max") is None or wert >= tl["max"] * anteil


def score(erg: Ergebnis, pd: PunkteDaten):
    prof, dec = erg.profil, erg.dec
    mit = prof.get("mitarbeit", {})
    anteil = prof.get("positiv_anteil_standard", 0.5)
    ausstehend = bool(erg.termine_spaeter)
    for s in erg.students:
        dec.stumm = s.abgelegt
        r = StudentErgebnis(s)
        pflicht, fehl = pflicht_status(erg, s)
        r.pflicht, r.abwesend = pflicht, fehl
        r.anwesend = len(erg.termine) - fehl
        r.mitarbeit = sum(erg.mia.get(s.key, {}).values())
        mia_pkt = r.mitarbeit * mit.get("punkte_pro_eintrag", 1) * mit.get("multiplikator", 1)
        hat_daten = (s.key in erg.anw or s.key in erg.mia or s.key in pd.gefunden
                     or any(dec.get(qid("manuell", s.key, tl["id"])) not in (None, "") for tl in prof["teilleistungen"]))
        override = dec.get(qid("status", s.key))
        if override is None and not hat_daten:
            override = dec.ask(qid("status", s.key), PERSON,
                               f"{s.anzeige}: keinerlei Logbuch-Einträge oder Punkte. Welcher Status gilt?",
                               [("ausgestiegen", "ausgestiegen"), ("unbeurteilt", "unbeurteilt"),
                                ("normal", "normal bewerten (fehlende Leistungen negativ)")])
        for name in erg.cfg.get("textfelder") or {}:
            vals = pd.texte.get((s.key, name), [])
            r.texte[name] = "\n".join(dict.fromkeys(vals))
        for tl in prof["teilleistungen"]:
            te = Teilergebnis(tl["id"], tl.get("name", tl["id"]), tl.get("max"), zusatz=bool(tl.get("zusatz")))
            typ = tl["typ"]
            if typ == "liste":
                vals = pd.werte.get((s.key, tl["id"]), [])
                distinct = sorted({v for v, _ in vals})
                if not distinct:
                    te.status = "fehlt"
                elif len(distinct) == 1:
                    te.wert, te.quelle = distinct[0], "; ".join(q for _, q in vals)
                else:
                    a = dec.ask(qid("konflikt", s.key, tl["id"], *distinct), PERSON,
                                f"{s.anzeige}, {te.name}: unterschiedliche Werte "
                                f"({'; '.join(f'{fmt(v)} in {q}' for v, q in vals)}). Welcher gilt?",
                                [(str(v), fmt(v)) for v in distinct])
                    te.wert, te.status = (float(a), "ok") if a is not None else (None, "offen")
                if te.wert is not None and tl.get("faktor") not in (None, 1):
                    te.wert = te.wert * float(tl["faktor"])       # Gewichtung der Rohpunkte
                if te.wert is not None and not te.zusatz and (te.wert < 0 or (te.max is not None and te.wert > te.max)):
                    a = dec.ask(qid("bereich", s.key, tl["id"], te.wert), PERSON,
                                f"{s.anzeige}, {te.name}: {fmt(te.wert)} liegt außerhalb von 0 bis {fmt(te.max)}.",
                                [("ok", "Wert übernehmen"), ("weg", "Wert nicht verwenden (fehlt)")])
                    if a == "weg":
                        te.wert, te.status = None, "fehlt"
                    elif a is None:
                        te.status = "offen"
            elif typ == "manuell":
                a = dec.get(qid("manuell", s.key, tl["id"]))
                num = parse_number(a) if a not in (None, "") else None
                if num is None:
                    te.status = "ok" if te.zusatz else "fehlt"
                else:
                    te.wert, te.quelle = num, "Eingabe in der App"
            else:
                if typ == "anwesenheit":
                    wert = {"erfüllt": tl["max"], "nicht erfüllt": 0}.get(pflicht)
                elif typ == "mitarbeit":
                    wert = min(tl["max"], mia_pkt) if tl.get("kappen", True) and tl.get("max") else mia_pkt
                    if not tl.get("kappen", True) and tl.get("max") and mia_pkt > tl["max"]:
                        erg.protokoll.append(("Mitarbeit", s.anzeige, f"{fmt(mia_pkt)} Punkte über dem Maximum {fmt(tl['max'])} (nicht gekappt)"))
                else:
                    a_pkt = tl.get("anwesenheit_punkte", tl["max"] / 2)
                    basis = {"erfüllt": a_pkt, "nicht erfüllt": 0}.get(pflicht)
                    wert = None if basis is None else basis + min(tl["max"] - a_pkt, mia_pkt)
                te.wert = float(wert) if wert is not None else None
                if te.wert is None:
                    te.status = "offen" if pflicht == "offen" else "vorläufig"
                elif ausstehend:
                    te.status = "vorläufig"
                te.quelle = "Logbuch"
                for alias in tl.get("kontrolle", []):
                    for v, q in pd.kontrolle.get((s.key, alias), []):
                        num = parse_number(v)
                        if te.wert is not None and num is not None and abs(num - te.wert) > 0.001:
                            berechnet = fmt(te.wert)
                            a = dec.ask(qid("kontrolle", s.key, tl["id"], te.wert, num), PERSON,
                                        f"{s.anzeige}, {te.name}: berechnet {fmt(te.wert)}, in {q} steht {fmt(num)}. Welcher Wert gilt?",
                                        [("berechnet", f"berechneter Wert {fmt(te.wert)}"), ("datei", f"Wert aus Datei {fmt(num)}")])
                            erg.kontrolle.append((s.anzeige, te.name, berechnet, fmt(num), q,
                                                  {"datei": "Wert aus Datei gilt", "berechnet": "berechneter Wert gilt"}.get(a, "offen")))
                            if a == "datei":
                                te.wert, te.quelle = num, q
                            elif a is None:
                                te.status = "offen"
            if te.wert is not None and not te.zusatz:
                te.positiv = _positiv(tl, te.wert, pflicht, anteil)
            r.teile[tl["id"]] = te
        _gesamt(erg, r, override, pd)
        erg.ergebnisse[s.key] = r
    dec.stumm = False


def _gesamt(erg: Ergebnis, r: StudentErgebnis, override, pd: PunkteDaten):
    prof = erg.profil
    r.summe = sum(t.wert for t in r.teile.values() if t.wert is not None)
    pflicht_teile = [t for t in r.teile.values() if not t.zusatz]
    fehlend = [t for t in pflicht_teile if t.wert is None]
    negativ = [t for t in pflicht_teile if t.positiv is False]
    offen = [t for t in r.teile.values() if t.status in ("offen", "vorläufig")]
    schluessel = prof.get("notenschluessel")
    for t in fehlend:
        r.hinweise.append(f"Die Teilleistung „{t.name}“ liegt noch nicht vor." if t.status == "fehlt"
                          else f"Die Teilleistung „{t.name}“ ist noch nicht abschließend festgestellt.")
    for t in negativ:
        r.hinweise.append(f"Die Teilleistung „{t.name}“ ist nicht positiv ({fmt(t.wert)} von {fmt(t.max)}).")
    if erg.termine_spaeter and not fehlend:
        r.hinweise.append(f"Es stehen noch {len(erg.termine_spaeter)} Termine aus; Anwesenheit und Mitarbeit sind vorläufig.")
    if override in ("ausgestiegen", "unbeurteilt"):
        r.note, r.status = STATUS_WERTE[override], STATUS_WERTE[override]
        return
    if not schluessel:
        r.note = ""
        r.status = "keine Note (Profil ohne Notenschlüssel)"
    elif negativ or fehlend:
        r.note = prof.get("negativ_note", "5 (NG)")
    else:
        r.note = note_fuer(r.summe, schluessel)
    if override in ("vorlaeufig", "nachfrist", "endgueltig"):
        r.status = STATUS_WERTE[override]
    elif schluessel:
        r.status = "vorläufig" if (fehlend or offen or erg.termine_spaeter) else "endgültig"
    for alias in prof.get("kontrollspalten_gesamt", []):
        for v, q in pd.kontrolle.get((r.student.key, alias), []):
            num = parse_number(v)
            if num is not None and abs(num - r.summe) > 0.001:
                erg.kontrolle.append((r.student.anzeige, "Gesamtpunkte", fmt(r.summe), fmt(num), q, "Hinweis"))
    for alias in prof.get("kontrollspalten_note", []):
        for v, q in pd.kontrolle.get((r.student.key, alias), []):
            if r.note and str(v).strip() != r.note:
                erg.kontrolle.append((r.student.anzeige, "Note", r.note, str(v), q, "Hinweis"))


# --------------------------------------------------------------------------- Gesamtlauf

def run(kurs_dir: Path, dec: Decisions | None = None, stichtag: date | None = None) -> Ergebnis:
    kurs_dir = Path(kurs_dir).resolve()
    cfg = C.load_course(kurs_dir)
    if not cfg.get("profil"):
        raise ValueError("In kurs.json ist kein Bewertungsprofil eingetragen.")
    profil = C.load_profile(C.root_of(kurs_dir), cfg["profil"])
    dec = dec or C.decisions_for(kurs_dir)
    dec.open.clear()
    # Antworten zum gemeinsamen Logbuch gelten für alle Lehrveranstaltungen des Semesters
    dec.fallback = {}
    for nachbar in sorted(C.semester_of(kurs_dir).iterdir()):
        if nachbar.is_dir() and nachbar != kurs_dir and C.is_course_dir(nachbar):
            for k_, v_ in C.decisions_for(nachbar).answers.items():
                if v_.get("semesterweit"):
                    dec.fallback.setdefault(k_, v_["antwort"])
    erg = Ergebnis(kurs_dir, cfg, profil, dec)
    if stichtag is None:
        stichtag = date.fromisoformat(cfg["stichtag"]) if cfg.get("stichtag") else date.today()
    kursname = cfg.get("bezeichnung") or kurs_dir.name

    rollen = assign_roles(load_tables(kurs_dir, erg), profil, dec, erg.protokoll)
    # Studentenliste und Termine nur aus dem Kursordner, nicht aus dem Semesterordner
    st_tabs = [t for t in rollen["studenten"] if not t.source.startswith("../")]
    te_tabs = [t for t in rollen["termine"] if not t.source.startswith("../")]
    erg.students = load_students(st_tabs, erg)
    idx = StudentIndex(erg.students)
    idx.lerne(dec)
    erg.bereinigung = idx.statistik

    alle = load_termine(te_tabs, cfg, dec, erg, kursname)
    log_cfg = dict(cfg)
    for rel in cfg.get("verbunden_mit") or []:
        vdir = (kurs_dir / rel).resolve()
        vcfg = C.load_course(vdir)
        vdec = C.decisions_for(vdir)
        vrollen = assign_roles([t for t in load_tables(vdir, erg) if not t.source.startswith("../")], profil, vdec, [])
        alle += load_termine(vrollen["termine"], vcfg, vdec, erg, vcfg.get("bezeichnung") or vdir.name)
        log_cfg["logbuch_lv"] = list(cfg.get("logbuch_lv") or []) + list(vcfg.get("logbuch_lv") or [])
        if vdec.open:
            erg.warnungen.append(f"Verbundene LV {vdir.name} hat offene Fragen; bitte dort zuerst klären.")
    eindeutig = {}
    for t in alle:
        eindeutig.setdefault(t.datum, t)
    alle = sorted(eindeutig.values(), key=lambda t: t.datum)
    erg.termine = [t for t in alle if t.datum <= stichtag]
    erg.termine_spaeter = [t for t in alle if t.datum > stichtag]
    if not alle:
        erg.warnungen.append("Keine offiziellen Termine gefunden.")

    load_logbook(rollen["logbuch"], log_cfg, dec, idx, alle, erg, kursname)
    for (name, n) in sorted(idx.fremd.items(), key=lambda x: -x[1]):
        erg.protokoll.append(("Nicht in Studentenliste", name, f"{n} Einträge nicht gewertet (keine Person der offiziellen Liste)"))
    idx.fremd.clear()
    pd = load_points(rollen["punkte"], erg, idx)
    score(erg, pd)
    _zugaenge(erg)
    return erg


def _zugaenge(erg: Ergebnis):
    """Merkt sich die bekannten Studenten und meldet, wer seit dem letzten Lauf neu auf der Liste steht."""
    import json
    p = erg.kurs_dir / C.APPDIR / "bekannte_studenten.json"
    alt = set(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else None
    jetzt = {s.key for s in erg.students}
    if alt is not None:
        erg.neu_studenten = [s.anzeige for s in erg.students if s.key not in alt]
        for n in erg.neu_studenten:
            erg.protokoll.append(("Studentenliste", n, "neu aufgenommen"))
    if erg.students and jetzt != alt:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(jetzt | (alt or set()))), encoding="utf-8")
