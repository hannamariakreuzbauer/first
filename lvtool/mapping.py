"""Erkennung von Tabellenrollen und Spalten. Unklare Fälle werden als Strukturfrage gestellt."""
from __future__ import annotations

from .decisions import STRUKTUR, Decisions, qid
from .io_tables import Table, normalize_header

ROLLEN = {
    "studenten": "offizielle Studentenliste",
    "termine": "offizielle Terminliste",
    "logbuch": "Logbuch (Anwesenheit/Mitarbeit)",
    "punkte": "Punkteliste",
    "ignorieren": "nicht verwenden",
}

# Feld -> Synonyme (normalisiert). Felder mit Endung "2" sind Wiederholungsfelder.
FELDER = {
    "studenten": {
        "matrikel": ["matrikelnummer", "matrikelnr", "matnr", "matrikel", "studentid"],
        "nachname": ["familienname", "nachname", "famname", "lastname", "surname", "zuname"],
        "vorname": ["vorname", "prename", "firstname", "givenname"],
        "email": ["email", "emailadresse", "mail", "eMail"],
        "studium": ["studium"],
        "kennzahl": ["kennzahl"],
        "studienid": ["studienid", "studid"],
        "gruppe": ["gruppe"],
        "platz": ["platz"],
        "anrede": ["anrede"],
    },
    "termine": {
        "datum": ["datum", "date", "termin"],
        "von": ["von", "beginn", "start"],
        "bis": ["bis", "ende", "end"],
        "lv": ["lvnummer", "lvnr", "lehrveranstaltung", "lv"],
        "titel": ["titel"],
        "art": ["lvart"],
        "gruppe": ["lvgruppe", "gruppe"],
        "anmerkung": ["anmerkung", "bemerkung"],
    },
    "logbuch": {
        "zeitstempel": ["zeitstempel", "timestamp"],
        "nachname": ["familienname", "nachname", "famname", "lastname"],
        "vorname": ["vorname", "prename", "firstname"],
        "email": ["emailadresse", "email", "mail"],
        "matrikel2": ["matrikelnummerwiederholung"],
        "matrikel": ["matrikelnummer", "matrikelnr", "matrikel"],
        "lv": ["lehrveranstaltung", "lv", "kurs"],
        "art": ["artdeseintrags", "art", "typ"],
        "datum2": ["datumderbetreffendenstundewiederholung"],
        "datum": ["datumderbetreffendenstunde", "datum", "date"],
        "anwesenheit": ["anwesenheit"],
        "mitarbeit": ["mitarbeit"],
        "text": ["text", "anmerkung", "kommentar"],
        "upload": ["upload"],
    },
    "punkte": {
        "matrikel": ["matrikelnummer", "matrikelnr", "matnr", "matrikel"],
        "email": ["email", "emailadresse", "mail"],
        "nachname": ["familienname", "nachname", "famname", "lastname"],
        "vorname": ["vorname", "prename", "firstname"],
        "codenummer": ["codenummer", "codno", "code"],
    },
}

PFLICHT = {
    "studenten": [["matrikel"], ["nachname"], ["vorname"], ["email"]],
    "termine": [["datum"]],
    "logbuch": [["datum"], ["matrikel", "email", "nachname"], ["anwesenheit", "mitarbeit"]],
    "punkte": [["matrikel", "email", "nachname", "codenummer"]],
}

FELD_LABEL = {
    "matrikel": "Matrikelnummer", "matrikel2": "Matrikelnummer (Wiederholung)", "nachname": "Familienname",
    "vorname": "Vorname", "email": "E-Mail-Adresse", "datum": "Datum", "datum2": "Datum (Wiederholung)",
    "anwesenheit": "Anwesenheit", "mitarbeit": "Mitarbeit", "codenummer": "Codenummer",
}


def signatur(t: Table) -> str:
    return (t.sheet or "") + "|" + ",".join(sorted(normalize_header(h) for h in t.headers))


def _score(header_n: str, syn: str) -> int:
    syn = normalize_header(syn)
    if header_n == syn:
        return 3
    if len(syn) >= 5 and header_n.startswith(syn):
        return 2
    return 0


def detect_fields(t: Table, rolle: str) -> dict[str, str]:
    found: dict[str, str] = {}
    used: set[str] = set()
    for feld, syns in FELDER.get(rolle, {}).items():
        best, best_s = None, 0
        for h in t.headers:
            if h in used:
                continue
            hn = normalize_header(h)
            if not feld.endswith("2") and "wiederholung" in hn:
                continue
            s = max((_score(hn, x) for x in syns), default=0)
            if s > best_s:
                best, best_s = h, s
        if best:
            found[feld] = best
            used.add(best)
    return found


def alias_matches(alias: str, t: Table, header: str) -> bool:
    """Alias "SPALTE" passt auf jede Tabelle, "BLATT:SPALTE" nur auf dieses Blatt."""
    if ":" in alias:
        sheet, col = alias.split(":", 1)
        if normalize_header(t.sheet or "") != normalize_header(sheet):
            return False
    else:
        col = alias
    return normalize_header(header) == normalize_header(col)


def find_alias_column(t: Table, aliases: list[str]) -> str | None:
    for a in aliases:
        for h in t.headers:
            if alias_matches(a, t, h):
                return h
    return None


def suggest_role(t: Table, profil: dict) -> str | None:
    n = {normalize_header(h) for h in t.headers}
    f_log = detect_fields(t, "logbuch")
    if "datum" in f_log and ("anwesenheit" in f_log or "mitarbeit" in f_log) and "lv" in f_log:
        return "logbuch"
    comp_aliases = [a for tl in profil.get("teilleistungen", []) for a in tl.get("spalten", [])]
    if any(find_alias_column(t, [a]) for a in comp_aliases):
        return "punkte"
    f_st = detect_fields(t, "studenten")
    if all(k in f_st for k in ("matrikel", "nachname", "vorname", "email")):
        return "studenten"
    f_te = detect_fields(t, "termine")
    if "datum" in f_te and ("von" in f_te or "bis" in f_te) and "matrikel" not in f_st:
        return "termine"
    if n & {"score", "punkte", "bewertung"}:
        return "punkte"
    return None


def _ist_matrix(t: Table) -> bool:
    import re
    datum_spalten = [h for h in t.headers if re.search(r"\d{4}[.\-]\d{2}[.\-]\d{2}", h)]
    return len(datum_spalten) >= 3


def _einzeldatei(t: Table, tables: list[Table]) -> bool:
    return sum(1 for x in tables if x.source == t.source) == 1


def assign_roles(tables: list[Table], profil: dict, dec: Decisions, protokoll: list) -> dict[str, list[Table]]:
    """Ordnet jeder Tabelle eine Rolle zu. Eindeutige Fälle automatisch, sonst Frage."""
    vorschlag = {id(t): suggest_role(t, profil) for t in tables}
    counts: dict[str, int] = {}
    for r in vorschlag.values():
        if r:
            counts[r] = counts.get(r, 0) + 1
    out: dict[str, list[Table]] = {r: [] for r in ROLLEN}
    for t in tables:
        sig = signatur(t)
        q = qid("rolle", sig)
        stored = dec.get(q)
        if not stored and t.source.startswith("../"):
            dec.semesterweit.add(q)
            stored = dec.fallback.get(q)
        if stored:
            rolle = stored
        else:
            v = vorschlag[id(t)]
            eindeutig = v in ("termine", "punkte") or (v in ("studenten", "logbuch") and counts.get(v) == 1)
            if v == "studenten" and not eindeutig:
                einzeln = [x for x in tables if vorschlag[id(x)] == "studenten" and _einzeldatei(x, tables)]
                if len(einzeln) == 1:
                    if einzeln[0] is t:
                        eindeutig = True
                        protokoll.append(("Rolle", t.label, "als offizielle Studentenliste bevorzugt (eigenständige Datei)"))
                    else:
                        rolle = "ignorieren"
                        protokoll.append(("Rolle", t.label, f"nicht verwendet: {einzeln[0].label} ist die offizielle Studentenliste"))
                        out.setdefault(rolle, []).append(t)
                        continue
            if v and eindeutig:
                rolle = v
                protokoll.append(("Rolle", t.label, f"automatisch als {ROLLEN[v]} erkannt"))
            else:
                opts = []
                if v:
                    opts.append((v, f"{ROLLEN[v]} (Vorschlag)"))
                opts += [(k, lbl) for k, lbl in ROLLEN.items() if k != v]
                if v == "ignorieren":
                    opts = [o for i, o in enumerate(opts) if o[0] != "ignorieren" or i == 0]
                spalten = ", ".join(t.headers[:12]) + (" …" if len(t.headers) > 12 else "")
                if v:
                    grund = f"Mehrere Tabellen kommen als {ROLLEN[v]} in Frage."
                elif _ist_matrix(t):
                    grund = "Sie sieht wie eine bereits abgeleitete Anwesenheits-/Mitarbeitsübersicht aus."
                    v = "ignorieren"
                    opts = [("ignorieren", "nicht verwenden (Vorschlag)")]
                else:
                    grund = "Die Rolle ist nicht erkennbar."
                dec.ask(q, STRUKTUR, f"Wofür ist die Tabelle {t.label} zu verwenden? {grund} Spalten: {spalten}",
                        opts, {"datei": t.source, "blatt": t.sheet})
                continue
        out.setdefault(rolle, []).append(t)
    return out


def resolve_fields(t: Table, rolle: str, dec: Decisions) -> dict[str, str] | None:
    """Spaltenzuordnung inkl. gespeicherter Antworten; None, wenn Pflichtfelder offen sind."""
    fields = detect_fields(t, rolle)
    sig = signatur(t)
    complete = True
    for gruppe in PFLICHT.get(rolle, []):
        if any(f in fields for f in gruppe):
            continue
        feld = gruppe[0]
        q = qid("spalte", rolle, feld, sig)
        a = dec.ask(q, STRUKTUR,
                    f"In {t.label} ({ROLLEN[rolle]}): Welche Spalte enthält „{FELD_LABEL.get(feld, feld)}“?",
                    [(h, h) for h in t.headers] + [("__keine__", "keine solche Spalte")],
                    {"datei": t.source, "blatt": t.sheet})
        if a is None:
            complete = False
        elif a != "__keine__":
            fields[feld] = a
    return fields if complete else None
