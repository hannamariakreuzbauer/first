"""Abgleich von Einträgen mit der offiziellen Studentenliste."""
from __future__ import annotations

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
    abgelegt: bool = False        # in der Ablage: dokumentiert, aber nicht mehr in Ergebnissen und E-Mails
    nicht_aktuell: bool = False   # steht nicht mehr auf der neuesten Teilnehmerliste

    @property
    def anzeige(self) -> str:
        return f"{self.nachname}, {self.vorname} ({self.matrikel})"


def osa(a: str, b: str) -> int:
    """Editierabstand mit Buchstabendrehern (optimal string alignment)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la or not lb:
        return la or lb
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            k = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + k)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def skelett(tok: str) -> str:
    """Vergleichsform eines Namensteils: Umlaut-Umschreibungen und Doppelbuchstaben vereinheitlicht."""
    for x, y in (("ae", "a"), ("oe", "o"), ("ue", "u"), ("ph", "f"), ("th", "t"), ("y", "i"), ("ck", "k")):
        tok = tok.replace(x, y)
    return re.sub(r"(.)\1+", r"\1", tok)


def namens_teile(*parts) -> list[str]:
    """Alle Namensteile in Vergleichsform (auch Initialen), Reihenfolge und Feldaufteilung egal."""
    return sorted({skelett(t) for p in parts for t in norm_text(p).split() if t})


def _teil_aehnlichkeit(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) == 1 or len(b) == 1:                       # Initiale
        return 0.9 if a[0] == b[0] else 0.0
    if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 0.85                                      # Kurzform: Alex/Alexander
    return max(0.0, 1 - osa(a, b) / max(len(a), len(b)))


def namens_aehnlichkeit(e: list[str], s: list[str]) -> tuple[float, str]:
    """Ähnlichkeit zweier Namen (0 bis 1) und Art: exakt | teil | aehnlich | keine."""
    if not e or not s:
        return 0.0, "keine"
    if e == s:
        return 1.0, "exakt"
    kurz, lang = (e, s) if len(e) <= len(s) else (s, e)
    frei = list(lang)
    werte = []
    for t in kurz:
        if not frei:
            break
        best = max(frei, key=lambda x: _teil_aehnlichkeit(t, x))
        werte.append((_teil_aehnlichkeit(t, best), t, best))
        frei.remove(best)
    if not werte or not any(w >= 0.8 and len(t) >= 3 and len(b) >= 3 for w, t, b in werte):
        return 0.0, "keine"
    score = sum(w for w, _, _ in werte) / len(kurz) * (1 - 0.03 * (len(lang) - len(kurz)))
    art = "teil" if all(w == 1.0 for w, _, _ in werte) else "aehnlich"
    return score, art


STARK, MITTEL = 3, 2


class StudentIndex:
    """Abgleich von Einträgen mit der offiziellen Liste: mehrere Merkmale werden gemeinsam gewertet.
    Angenommen wird nur bei hinreichenden, widerspruchsfreien Belegen; sonst entscheidet die Benutzerin."""

    def __init__(self, students: list[Student]):
        self.students = students
        self.by_key = {s.key: s for s in students}
        self.by_email = {}
        for s in students:
            e = norm_email(s.email)
            if e:
                self.by_email[e] = s
        self.by_code: dict[str, Student] = {}
        self.fremd: dict[str, int] = {}
        self.teile = {s.key: namens_teile(s.nachname, s.vorname) for s in students}
        self.alias_email: dict[str, str] = {}
        self.alias_matrikel: dict[str, str] = {}
        self.statistik = {"exakt": 0, "bereinigt": 0, "frage": 0, "datum": 0}
        self._memo: dict = {}

    def lerne(self, dec: Decisions):
        """Bereits bestätigte Zuordnungen gelten als lokale Aliasse (E-Mail- und Matrikel-Varianten)."""
        for v in dec.answers.values():
            k = v.get("kontext") or {}
            if k.get("art") != "zuordnung" or v.get("antwort") not in self.by_key:
                continue
            e = k.get("eintrag", {})
            if norm_email(e.get("email")):
                self.alias_email.setdefault(norm_email(e["email"]), v["antwort"])
            for m in e.get("matrikel_norm", []):
                self.alias_matrikel.setdefault(m, v["antwort"])

    def register_code(self, s: Student, code):
        c = norm_text(code)
        if c:
            s.codenummer.add(c)
            self.by_code[c] = s

    def _namen(self, teile: list[str]) -> list[tuple[float, str, Student]]:
        out = []
        for s in self.students:
            sc, art = namens_aehnlichkeit(teile, self.teile[s.key])
            if sc > 0:
                out.append((sc, art, s))
        return sorted(out, key=lambda x: -x[0])

    def resolve(self, dec: Decisions, protokoll: list, quelle: str, *, matrikel=(), email=None,
                nachname=None, vorname=None, codenummer=None) -> Student | None:
        mats = [m for m in dict.fromkeys(norm_matrikel(x) for x in matrikel) if m]
        raw_mats = [str(x) for x in matrikel if x not in (None, "")]
        mail = norm_email(email)
        teile = namens_teile(nachname, vorname)
        code = norm_text(codenummer) or None
        if not (mats or mail or teile or code):
            return None
        ident = (tuple(sorted(mats)), mail, " ".join(teile), code)
        if ident in self._memo:                      # dieselbe Schreibweise wurde in diesem Lauf schon entschieden
            art, wert = self._memo[ident]
            if art == "ok":
                self.statistik[wert[1]] += 1
                return self.by_key[wert[0]]
            if art == "fremd":
                self.fremd[wert] = self.fremd.get(wert, 0) + 1
                return None

        belege: dict[str, list[tuple[int, str]]] = {}

        def beleg(key, staerke, text):
            belege.setdefault(key, []).append((staerke, text))

        namen = self._namen(teile) if teile else []
        name_sc = {s.key: sc for sc, _, s in namen}
        buchstaben = sum(len(t) for t in teile)

        # 1. stabile Merkmale
        for m in mats:
            if m in self.by_key:
                beleg(m, STARK, "Matrikelnummer")
            elif m in self.alias_matrikel:
                beleg(self.alias_matrikel[m], STARK, "bereits bestätigte Matrikel-Variante")
            else:   # Tippfehler in der Matrikelnummer zählt nur zusammen mit passendem Namen
                nah = [k for k in self.by_key if abs(len(k) - len(m)) <= 1 and osa(k, m) == 1 and name_sc.get(k, 0) >= 0.6]
                if len(nah) == 1:
                    beleg(nah[0], MITTEL, f"Matrikelnummer mit Tippfehler ({m})")
        if mail:
            if mail in self.by_email:
                beleg(self.by_email[mail].key, STARK, "E-Mail")
            elif mail in self.alias_email:
                beleg(self.alias_email[mail], STARK, "bereits bestätigte E-Mail-Adresse")
            else:
                lokal, _, dom = mail.partition("@")
                nah = [s.key for e, s in self.by_email.items()
                       if e.partition("@")[2] == dom and osa(e.partition("@")[0], lokal) == 1]
                if len(nah) == 1:
                    beleg(nah[0], MITTEL, "E-Mail-Adresse mit Tippfehler")
                else:   # private Adresse, deren Namensteil zu genau einem Studenten passt
                    lt = namens_teile(re.sub(r"[\d._\-+]+", " ", lokal))
                    tr = [s.key for s in self.students if len(lt) >= 2 and namens_aehnlichkeit(lt, self.teile[s.key])[0] >= 0.95]
                    if len(tr) == 1:
                        beleg(tr[0], MITTEL, "Name in der E-Mail-Adresse")
        if code and code in self.by_code:
            beleg(self.by_code[code].key, STARK, "Codenummer")

        # 2. Name: exakt, Teilname (fehlender Zweitname, Initiale), ähnlich (Tippfehler) mit Abstand zum Nächstbesten
        if namen:
            sc1, art1, s1 = namen[0]
            sc2 = namen[1][0] if len(namen) > 1 else 0.0
            if art1 == "exakt" and sc2 < 1.0:
                beleg(s1.key, STARK, "Name")
            elif sc1 >= 0.86 and sc1 - sc2 >= 0.08 and (art1 == "teil" or buchstaben >= 6):
                beleg(s1.key, MITTEL, "Teilname" if art1 == "teil" else "Name mit Schreibabweichung")

        # 3. Entscheidungsregel
        stark = [k for k, b in belege.items() if any(x == STARK for x, _ in b)]
        mittel = [k for k, b in belege.items() if k not in stark]
        kandidat = None

        def fremde_matrikel(k):
            """Eine in sich stimmige Matrikelnummer, die zu niemandem passt, spricht für eine andere Person
            gleichen Namens (z. B. aus einer anderen Gruppe). Eine private E-Mail-Adresse spricht nicht dagegen."""
            texte = [t for _, t in belege[k]]
            einheitlich = len(mats) == 1 and (len(raw_mats) >= 2 or not mail)
            return einheitlich and not any("Matrikel" in t for t in texte) and not any("E-Mail" in t for t in texte)

        if len(stark) == 1 and not mittel:
            k = stark[0]
            arten = {t for x, t in belege[k] if x == STARK}
            if arten == {"Name"}:
                if not fremde_matrikel(k):
                    kandidat = k
            # ein einzelnes starkes Merkmal bei völlig anderem Namen ist ein Widerspruch
            elif not (teile and len(belege[k]) == 1 and name_sc.get(k, 0) < 0.5):
                kandidat = k
        elif not stark and len(mittel) == 1:
            k = mittel[0]
            texte = [t for _, t in belege[k]]
            if len(texte) >= 2 or not fremde_matrikel(k):
                kandidat = k
        if kandidat:
            s = self.by_key[kandidat]
            texte = [t for _, t in belege[kandidat]]
            sauber = all(t in ("Matrikelnummer", "E-Mail", "Name", "Codenummer") for t in texte) and \
                not (teile and name_sc.get(kandidat, 0) < 1.0) and all(m in self.by_key for m in mats)
            self._memo[ident] = ("ok", (kandidat, "exakt" if sauber else "bereinigt"))
            if sauber:
                self.statistik["exakt"] += 1
            else:
                self.statistik["bereinigt"] += 1
                roh = " ".join(filter(None, [str(nachname or ""), str(vorname or ""), "/".join(raw_mats), str(email or "")]))
                protokoll.append(("Bereinigt", quelle, f"„{roh}“ → {s.anzeige} (Belege: {', '.join(texte)})"))
            return s

        # 4. keine plausible Person dieser Lehrveranstaltung: nicht fragen, zusammengefasst protokollieren
        if not belege and (not namen or namen[0][0] < 0.8):
            name = f"{nachname or ''} {vorname or ''}".strip() or (raw_mats[0] if raw_mats else str(email or codenummer))
            if "Zeile" in quelle and "../" in quelle:
                self.fremd[name] = self.fremd.get(name, 0) + 1
                self._memo[ident] = ("fremd", name)
            else:
                protokoll.append(("Nicht in Studentenliste", quelle, f"{name}: keine Person der offiziellen Liste – nicht gewertet"))
            return None

        # 5. echter Zweifelsfall: einmal je Identität vorlegen, mit Auswahlliste
        q = qid("identitaet", *ident)
        stored = dec.get(q)
        if stored is not None:
            return self.by_key.get(stored) if stored != "__ignorieren__" else None
        if q in dec.open:
            dec.open[q].kontext["anzahl"] += 1
            return None
        reihen = list(dict.fromkeys(stark + mittel + [s.key for sc, _, s in namen if sc >= 0.6]))[:6]
        if len(stark) > 1:
            grund = "Die Angaben verweisen auf verschiedene Studenten."
        elif stark or mittel:
            k = (stark + mittel)[0]
            grund = f"Für {self.by_key[k].anzeige} spricht nur: {', '.join(t for _, t in belege[k])}; die übrigen Angaben passen nicht."
        else:
            grund = "Der Name ähnelt Studenten der Liste, ist aber nicht eindeutig."
        kontext = {"art": "zuordnung", "quelle": quelle, "anzahl": 1, "grund": grund,
                   "eintrag": {"name": f"{nachname or ''} {vorname or ''}".strip(), "matrikel": ", ".join(dict.fromkeys(raw_mats)),
                               "matrikel_norm": mats, "email": str(email or ""), "codenummer": str(codenummer or "")},
                   "vorschlaege": [[k, self.by_key[k].anzeige] for k in reihen],
                   "alle": [[s.key, s.anzeige] for s in self.students]}
        e = kontext["eintrag"]
        beschr = "; ".join(filter(None, [f"Name {e['name']}" if e["name"] else "", f"Matrikelnummer {e['matrikel']}" if e["matrikel"] else "",
                                         f"E-Mail {e['email']}" if e["email"] else "", f"Codenummer {e['codenummer']}" if e["codenummer"] else ""]))
        if dec.ask(q, PERSON, f"Zuordnung unklar ({quelle}): {beschr}. {grund} Wem gehört der Eintrag?",
                   [(k, self.by_key[k].anzeige) for k in reihen] + [("__ignorieren__", "gehört nicht zu dieser Lehrveranstaltung (nicht werten)")],
                   kontext) is None and q in dec.open:
            self.statistik["frage"] += 1
        return None
