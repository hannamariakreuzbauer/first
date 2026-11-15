"""Abgleich von Einträgen mit der offiziellen Studentenliste."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from .decisions import PERSON, Decisions, qid


def norm_text(s) -> str:
    if s is None:
        return ""
    s = str(s).replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).lower()
    return " ".join(s.split())


def name_tokens(*parts) -> frozenset:
    return frozenset(t for p in parts for t in norm_text(p).split() if len(t) > 1)


def norm_matrikel(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = re.sub(r"\D", "", str(v))
    if not (6 <= len(s) <= 9):
        return None
    return s.lstrip("0") or None


def norm_email(v) -> str | None:
    if not v:
        return None
    s = str(v).strip().lower()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None


@dataclass
class Student:
    key: str                     # normalisierte Matrikelnummer
    matrikel: str                # wie in der offiziellen Liste
    nachname: str
    vorname: str
    email: str | None
    felder: dict = field(default_factory=dict)   # alle übrigen Felder der offiziellen Liste
    codenummer: set = field(default_factory=set)

    @property
    def anzeige(self) -> str:
        return f"{self.nachname}, {self.vorname} ({self.matrikel})"


class StudentIndex:
    def __init__(self, students: list[Student]):
        self.students = students
        self.by_key = {s.key: s for s in students}
        self.by_email = {}
        for s in students:
            e = norm_email(s.email)
            if e:
                self.by_email[e] = s
        self.by_code: dict[str, Student] = {}

    def register_code(self, s: Student, code):
        c = norm_text(code)
        if c:
            s.codenummer.add(c)
            self.by_code[c] = s

    def _by_name(self, toks: frozenset) -> list[Student]:
        if not toks:
            return []
        exact = [s for s in self.students if name_tokens(s.nachname, s.vorname) == toks]
        if exact:
            return exact
        return [s for s in self.students if toks <= name_tokens(s.nachname, s.vorname)]

    def _fuzzy(self, toks: frozenset, n=3, schwelle=0.6) -> list[Student]:
        if not toks:
            return []
        target = " ".join(sorted(toks))
        scored = []
        for s in self.students:
            cand = " ".join(sorted(name_tokens(s.nachname, s.vorname)))
            r = difflib.SequenceMatcher(None, target, cand).ratio()
            if r >= schwelle:
                scored.append((r, s))
        return [s for _, s in sorted(scored, key=lambda x: -x[0])[:n]]

    def resolve(self, dec: Decisions, protokoll: list, quelle: str, *, matrikel=(), email=None,
                nachname=None, vorname=None, codenummer=None) -> Student | None:
        """Ordnet einen Eintrag eindeutig zu oder stellt eine Frage (einmal je Identität)."""
        mats = [m for m in dict.fromkeys(norm_matrikel(x) for x in matrikel) if m]
        raw_mats = [str(x) for x in matrikel if x not in (None, "")]
        mail = norm_email(email)
        toks = name_tokens(nachname, vorname)
        code = norm_text(codenummer) or None

        hits: dict[str, Student | None] = {}
        for m in mats:
            hits[f"Matrikelnummer {m}"] = self.by_key.get(m)
        if mail:
            hits["E-Mail"] = self.by_email.get(mail)
        if code:
            hits["Codenummer"] = self.by_code.get(code)
        if toks:
            byname = self._by_name(toks)
            if len(byname) == 1 or not byname:
                hits["Name"] = byname[0] if byname else None
            else:  # mehrdeutiger Name: nur stimmig, wenn eine andere Angabe einen davon trifft
                others = [s for s in hits.values() if s]
                if not others:
                    hits["Name"] = None
                elif not any(s in byname for s in others):
                    hits["Name"] = None

        found = {id(s): s for s in hits.values() if s}
        agree = [k for k, s in hits.items() if s]
        if len(found) == 1:
            s = next(iter(found.values()))
            missing = [k for k, v in hits.items() if v is None]
            if not missing:
                return s
            if len(agree) >= 2 or (len(hits) == 1):
                protokoll.append(("Zuordnung", quelle,
                                  f"{s.anzeige} über {', '.join(agree)}; nicht passend: {', '.join(missing)}"))
                return s
        if not hits:
            return None

        kandidaten = list(found.values())
        nah = self._fuzzy(toks, schwelle=0.85)
        if not kandidaten and not nah:
            # keine plausible Person dieser Lehrveranstaltung: nicht fragen, aber protokollieren
            protokoll.append(("Nicht in Studentenliste", quelle,
                              "Eintrag gehört zu keiner Person der offiziellen Liste – nicht gewertet"))
            return None
        ident = (tuple(sorted(mats)), mail, " ".join(sorted(toks)), code)
        q = qid("identitaet", *ident)
        stored = dec.get(q)
        if stored is not None:
            return self.by_key.get(stored) if stored != "__ignorieren__" else None
        for s in self._fuzzy(toks):
            if s not in kandidaten:
                kandidaten.append(s)
        beschr = "; ".join(filter(None, [
            f"Matrikelnummer {', '.join(raw_mats)}" if raw_mats else "",
            f"E-Mail {email}" if email else "",
            f"Name {nachname or ''} {vorname or ''}".strip() if toks else "",
            f"Codenummer {codenummer}" if code else "",
        ]))
        if len(found) > 1:
            grund = "Die Angaben verweisen auf verschiedene Studenten."
        elif found:
            grund = f"Nur {', '.join(agree)} passt; die übrigen Angaben passen nicht."
        else:
            grund = "Kein Student der offiziellen Liste passt eindeutig."
        dec.ask(q, PERSON, f"Zuordnung unklar ({quelle}): {beschr}. {grund} Wem gehört der Eintrag?",
                [(s.key, s.anzeige) for s in kandidaten] + [("__ignorieren__", "niemandem (Einträge nicht werten)")],
                {"quelle": quelle})
        return None
