"""Ordnerstruktur, Kurskonfiguration und Profilbibliothek.

Eingangsordner/
  _profile/                 Profilbibliothek (Bewertungsschemata, JSON)
  _vorlagen/                Report-Vorlagen (Word)
  2025W/                    Semesterordner, enthält das semesterweite Logbuch
    JAT_EDV/                Lehrveranstaltungsordner mit den Originallisten
      _lvtool/              Arbeitsdaten der App (Konfiguration, Entscheidungen, Ausgabe)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .decisions import Decisions

APPDIR = "_lvtool"
PROFILDIR = "_profile"
VORLAGENDIR = "_vorlagen"
VORLAGEN_PAKET = Path(__file__).parent / "profil_vorlagen"

KURS_STANDARD = {
    "bezeichnung": "",
    "lv_titel": "",
    "profil": "",
    "logbuch_lv": [],            # Kürzel bzw. Bezeichnungen der LV im Logbuch, z. B. ["JAT"]
    "termin_lv": [],             # LV-Nummern in der Terminliste; leer = alle
    "verbunden_mit": [],         # weitere Kursordner (relativ), deren Termine gemeinsam zählen
    "vorlage": "",               # Word-Vorlage für den Report (relativ zu _vorlagen oder Kursordner)
    "betreff": "{LV_TITEL}: Student Report",
    "absender_name": "",
    "absender_email": "",
    "rueckmeldefrist": "",
    "textfelder": {},            # Platzhalter -> Spaltenaliase, z. B. {"KOMMENTAR": ["MAIN:COMM_EXT"]}
    "stichtag": "",              # leer = heute; spätere Termine gelten als ausstehend
}


def is_course_dir(p: Path) -> bool:
    return (p / APPDIR / "kurs.json").exists()


def root_of(course_dir: Path) -> Path:
    return course_dir.parent.parent


def semester_of(course_dir: Path) -> Path:
    return course_dir.parent


def init_root(root: Path):
    (root / PROFILDIR).mkdir(parents=True, exist_ok=True)
    (root / VORLAGENDIR).mkdir(parents=True, exist_ok=True)
    for f in VORLAGEN_PAKET.glob("*.json"):
        ziel = root / PROFILDIR / f.name
        if not ziel.exists():
            shutil.copy2(f, ziel)


def load_course(course_dir: Path) -> dict:
    cfg = dict(KURS_STANDARD)
    cfg.update(json.loads((course_dir / APPDIR / "kurs.json").read_text(encoding="utf-8")))
    return cfg


def save_course(course_dir: Path, cfg: dict):
    d = course_dir / APPDIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "kurs.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def decisions_for(course_dir: Path) -> Decisions:
    return Decisions(course_dir / APPDIR / "entscheidungen.json")


def create_course(course_dir: Path, *, von: Path | None = None, profil: str | None = None, **felder) -> dict:
    """Legt eine Lehrveranstaltung an; mit `von` als Kopie (ohne personenbezogene Entscheidungen)."""
    if is_course_dir(course_dir):
        raise FileExistsError(f"{course_dir} ist bereits eingerichtet.")
    course_dir.mkdir(parents=True, exist_ok=True)
    if von:
        cfg = load_course(von)
        cfg["stichtag"] = ""
        cfg["rueckmeldefrist"] = ""
        alt = decisions_for(von)
        neu = decisions_for(course_dir)
        neu.answers = alt.structural_only()
        neu.save()
        # eigene Fassung des Bewertungsprofils: Änderungen im neuen Semester lassen das alte unberührt
        root = root_of(course_dir)
        if cfg.get("profil") and not profil:
            import re
            pid = re.sub(r"[^A-Za-z0-9_.\-]+", "_", f"{course_dir.name}_{course_dir.parent.name}")
            if pid != cfg["profil"]:
                if pid not in list_profiles(root):
                    copy_profile(root, cfg["profil"], pid)
                cfg["profil"] = pid
    else:
        cfg = dict(KURS_STANDARD)
        cfg["bezeichnung"] = course_dir.name
    if profil:
        cfg["profil"] = profil
    cfg.update({k: v for k, v in felder.items() if v is not None})
    save_course(course_dir, cfg)
    return cfg


def list_profiles(root: Path) -> list[str]:
    ids = {p.stem for p in (root / PROFILDIR).glob("*.json")} if (root / PROFILDIR).exists() else set()
    return sorted(ids | {p.stem for p in VORLAGEN_PAKET.glob("*.json")})


def load_profile(root: Path, pid: str) -> dict:
    for p in (root / PROFILDIR / f"{pid}.json", VORLAGEN_PAKET / f"{pid}.json"):
        if p.exists():
            prof = json.loads(p.read_text(encoding="utf-8"))
            validate_profile(prof)
            return prof
    raise FileNotFoundError(f"Profil {pid} nicht gefunden. Vorhanden: {', '.join(list_profiles(root))}")


def copy_profile(root: Path, alt: str, neu: str) -> Path:
    prof = load_profile(root, alt)
    prof["id"] = neu
    prof["kopiert_von"] = alt
    ziel = root / PROFILDIR / f"{neu}.json"
    if ziel.exists():
        raise FileExistsError(f"Profil {neu} existiert bereits.")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    return ziel


TYPEN = {"liste", "anwesenheit", "mitarbeit", "anwesenheit_mitarbeit"}


def validate_profile(p: dict):
    ids = set()
    for t in p.get("teilleistungen", []):
        if t.get("typ") not in TYPEN:
            raise ValueError(f"Profil {p.get('id')}: Teilleistung {t.get('id')} hat unbekannten Typ {t.get('typ')}.")
        if t["id"] in ids:
            raise ValueError(f"Profil {p.get('id')}: Teilleistung {t['id']} doppelt.")
        ids.add(t["id"])
    grenzen = [g["unter"] for g in (p.get("notenschluessel") or []) if g.get("unter") is not None]
    if grenzen != sorted(grenzen):
        raise ValueError(f"Profil {p.get('id')}: Notenschlüssel nicht aufsteigend.")
