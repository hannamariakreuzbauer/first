"""Eigenständige App: lokaler Server (nur 127.0.0.1, mit Zugriffstoken) und eigenes Chrome-Fenster."""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import course as C
from .decisions import PERSON, qid
from .pipeline import STATUS_WERTE, Ergebnis, fmt, run

UI = Path(__file__).with_name("ui.html")
TOKEN = secrets.token_urlsafe(24)
LOCK = threading.RLock()
CACHE: dict[str, Ergebnis] = {}


# --------------------------------------------------------------------------- Einstellungen

def _settings_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config")
    return base / "lvtool" / "einstellungen.json"


def settings() -> dict:
    p = _settings_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def save_settings(s: dict):
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def wurzel() -> Path | None:
    w = settings().get("wurzel")
    return Path(w) if w else None


def _im_eingang(p: Path) -> Path:
    w = wurzel()
    p = Path(p).resolve()
    if not w or (w.resolve() not in p.parents and p != w.resolve()):
        raise PermissionError("Pfad liegt außerhalb des Eingangsordners.")
    return p


# --------------------------------------------------------------------------- Daten für die Oberfläche

def zustand() -> dict:
    w = wurzel()
    st = settings()
    out = {"version": __version__, "wurzel": str(w) if w else "", "semester": [], "profile": [], "vorlagen": [],
           "status_werte": STATUS_WERTE, "ueberwachen": st.get("ueberwachen", True), "autostart": bool(st.get("autostart")),
           "autostart_moeglich": sys.platform == "win32" and bool(getattr(sys, "frozen", False))}
    if not w or not w.exists():
        return out
    for sem in sorted((p for p in w.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))), reverse=True):
        kurse = []
        for k in sorted(p for p in sem.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
            cfg = C.load_course(k) if C.is_course_dir(k) else None
            erg = CACHE.get(str(k))
            kurse.append({"name": k.name, "pfad": str(k), "eingerichtet": bool(cfg),
                          "titel": (cfg or {}).get("lv_titel") or "", "profil": (cfg or {}).get("profil") or "",
                          "offen": len(erg.offen) if erg else None})
        out["semester"].append({"name": sem.name, "pfad": str(sem), "kurse": kurse,
                                "abgeschlossen": abgeschlossen(sem)})
    for pid in C.list_profiles(w):
        try:
            prof = C.load_profile(w, pid)
            out["profile"].append({"id": pid, "bezeichnung": prof.get("bezeichnung", ""),
                                   "zu_bestaetigen": prof.get("zu_bestaetigen", [])})
        except Exception as e:  # noqa: BLE001
            out["profile"].append({"id": pid, "bezeichnung": f"Fehler: {e}", "zu_bestaetigen": []})
    vd = w / C.VORLAGENDIR
    out["vorlagen"] = sorted(p.name for p in vd.glob("*.docx") if not p.name.startswith("~$")) if vd.exists() else []
    return out


MARKE = "_abgeschlossen.json"


def abgeschlossen(sem: Path) -> bool:
    return (sem / MARKE).exists()


def ergebnis_json(erg: Ergebnis) -> dict:
    teile = [{"id": t["id"], "name": t.get("name", t["id"]), "max": t.get("max"), "zusatz": bool(t.get("zusatz")),
              "typ": t["typ"]}
             for t in erg.profil["teilleistungen"]]
    rows = []
    for s in erg.students:
        r = erg.ergebnisse[s.key]
        rows.append({
            "key": s.key, "matrikel": s.matrikel, "nachname": s.nachname, "vorname": s.vorname, "email": s.email or "",
            "teile": {k: {"wert": fmt(t.wert) if t.wert is not None else "", "status": t.status, "positiv": t.positiv}
                      for k, t in r.teile.items()},
            "summe": fmt(r.summe), "note": r.note, "status": r.status, "pflicht": r.pflicht,
            "anwesend": r.anwesend, "abwesend": r.abwesend, "mitarbeit": r.mitarbeit,
            "hinweise": r.hinweise, "status_override": erg.dec.get(qid("status", s.key)) or "",
            "abgelegt": s.abgelegt, "nicht_aktuell": s.nicht_aktuell,
        })
    return {
        "kurs": str(erg.kurs_dir), "profil": erg.profil.get("id"), "profil_bezeichnung": erg.profil.get("bezeichnung", ""),
        "zu_bestaetigen": erg.profil.get("zu_bestaetigen", []),
        "teile": teile, "studenten": rows, "offen": erg.offen, "neu_studenten": erg.neu_studenten,
        "termine": [t.label for t in erg.termine], "termine_spaeter": [t.label for t in erg.termine_spaeter],
        "warnungen": erg.warnungen, "kontrolle": [list(k) for k in erg.kontrolle],
        "protokoll": [list(p) for p in erg.protokoll[:3000]], "protokoll_gesamt": len(erg.protokoll),
        "master": str(erg.kurs_dir / C.APPDIR / "ausgabe" / "masterliste.xlsx"),
    }


def verarbeiten(pfad: str, stichtag: str | None = None) -> dict:
    from .export_excel import write_master
    k = _im_eingang(Path(pfad))
    if abgeschlossen(k.parent):
        raise ValueError(f"Das Semester {k.parent.name} ist abgeschlossen. Zum Weiterarbeiten das Semester wieder öffnen.")
    with LOCK:
        dec = C.decisions_for(k)
        erg = run(k, dec, date.fromisoformat(stichtag) if stichtag else None)
        dec.save()
        write_master(erg, k / C.APPDIR / "ausgabe" / "masterliste.xlsx")
        CACHE[str(k)] = erg
        return ergebnis_json(erg)


def _erg(pfad: str) -> Ergebnis:
    k = str(_im_eingang(Path(pfad)))
    if k not in CACHE:
        verarbeiten(k)
    return CACHE[k]


# --------------------------------------------------------------------------- Aktionen

def aktion(name: str, d: dict):
    if name == "wurzel":
        p = Path(d["pfad"]).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Ordner {p} existiert nicht.")
        C.init_root(p)
        from .report import erstelle_standard_vorlage
        v = p / C.VORLAGENDIR / "report_standard.docx"
        if not v.exists():
            erstelle_standard_vorlage(v)
        s = settings()
        s["wurzel"] = str(p.resolve())
        save_settings(s)
        CACHE.clear()
        return zustand()
    if name == "semester_anlegen":
        w = wurzel()
        name_ = d["name"].strip()
        if not name_ or any(c in name_ for c in '\\/:*?"<>|'):
            raise ValueError("Ungültiger Semestername.")
        (w / name_).mkdir(exist_ok=True)
        return zustand()
    if name == "kurs_anlegen":
        w = wurzel()
        ziel = _im_eingang(w / d["semester"] / d["name"].strip())
        von = _im_eingang(Path(d["von"])) if d.get("von") else None
        profil = d.get("profil") or None
        if profil == "__neu__":
            import re
            profil = re.sub(r"[^A-Za-z0-9_.\-]+", "_", f"{d['name'].strip()}_{d['semester']}")
            if profil not in C.list_profiles(w):
                basis = C.load_profile(w, "STANDARD")
                basis.update({"id": profil, "bezeichnung": f"{d['name'].strip()} {d['semester']}"})
                (w / C.PROFILDIR / f"{profil}.json").write_text(json.dumps(basis, ensure_ascii=False, indent=2), encoding="utf-8")
        C.create_course(ziel, von=von, profil=profil,
                        logbuch_lv=[x.strip() for x in d.get("logbuch_lv", "").split(",") if x.strip()] or None,
                        lv_titel=d.get("lv_titel") or None,
                        vorlage=d.get("vorlage") or (None if von else "report_standard.docx"))
        return zustand()
    if name == "kurs_laden":
        k = _im_eingang(Path(d["pfad"]))
        return C.load_course(k)
    if name == "kurs_speichern":
        k = _im_eingang(Path(d["pfad"]))
        cfg = C.load_course(k)
        for key in C.KURS_STANDARD:
            if key in d["cfg"]:
                cfg[key] = d["cfg"][key]
        if cfg.get("stichtag"):
            date.fromisoformat(cfg["stichtag"])
        C.save_course(k, cfg)
        CACHE.pop(str(k), None)
        return cfg
    if name == "verarbeiten":
        return verarbeiten(d["pfad"], d.get("stichtag") or None)
    if name == "antwort":
        k = _im_eingang(Path(d["pfad"]))
        with LOCK:
            erg = _erg(str(k))
            q = erg.dec.open.get(d["id"])
            dec = C.decisions_for(k)
            dec.semesterweit = erg.dec.semesterweit
            dec.answer(d["id"], d["wert"], frage=q.frage if q else "", kategorie=q.kategorie if q else PERSON)
            dec.save()
            erg.dec.answers = dec.answers
            erg.dec.open.pop(d["id"], None)
        return {"ok": True, "rest": len(erg.dec.open)}
    if name == "status":
        k = _im_eingang(Path(d["pfad"]))
        with LOCK:
            dec = C.decisions_for(k)
            if d["wert"] in ("", "normal"):
                dec.answers.pop(qid("status", d["key"]), None)
                if d["wert"] == "normal":
                    dec.answer(qid("status", d["key"]), "normal", frage=f"Status {d['key']}", kategorie=PERSON)
            else:
                dec.answer(qid("status", d["key"]), d["wert"], frage=f"Status {d['key']}", kategorie=PERSON)
            dec.save()
        return verarbeiten(str(k))
    if name == "entscheidung_loeschen":
        k = _im_eingang(Path(d["pfad"]))
        with LOCK:
            dec = C.decisions_for(k)
            dec.answers.pop(d["id"], None)
            dec.save()
        return verarbeiten(str(k))
    if name == "entscheidungen":
        k = _im_eingang(Path(d["pfad"]))
        dec = C.decisions_for(k)
        return [{"id": i, **v} for i, v in sorted(dec.answers.items(), key=lambda x: x[1].get("zeit", ""), reverse=True)]
    if name == "entwuerfe":
        from . import report as R
        erg = _erg(d["pfad"])
        if erg.offen and not d.get("trotzdem"):
            raise ValueError(f"Es gibt noch {len(erg.offen)} offene Klärungen.")
        mails = R.entwuerfe(erg)
        ziel = erg.kurs_dir / C.APPDIR / "ausgabe"
        out = {"ohne_email": [m["student"].anzeige for m in mails if not m["an"]]}
        if d.get("art") == "outlook":
            out["outlook"] = R.outlook_entwuerfe([m for m in mails if m["an"]], erg.cfg.get("absender_email", ""))
        else:
            out["eml"] = len(R.schreibe_eml(mails, ziel / "entwuerfe", erg.cfg.get("absender_email", "")))
            x, c = R.schreibe_serienbrief(mails, ziel)
            out["ordner"] = str(ziel)
        return out
    if name == "oeffnen":
        p = _im_eingang(Path(d["pfad"]))
        if not p.exists():
            raise FileNotFoundError(f"{p} existiert (noch) nicht.")
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return {"ok": True}
    if name == "profil_laden":
        w = wurzel()
        prof = C.load_profile(w, d["id"])
        nutzer = [f"{s_['name']} / {k['name']}" for s_ in zustand()["semester"] for k in s_["kurse"] if k["profil"] == d["id"]]
        return {"profil": prof, "verwendet_von": nutzer}
    if name == "profil_speichern":
        import re
        w = wurzel()
        prof = d["profil"]
        pid = str(prof.get("id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", pid):
            raise ValueError("Der Profilname darf nur Buchstaben, Ziffern, Punkt, Binde- und Unterstrich enthalten.")
        if d.get("neu") and pid in C.list_profiles(w):
            raise FileExistsError(f"Ein Profil {pid} gibt es bereits.")
        for t in prof.get("teilleistungen", []):
            if not str(t.get("id", "")).strip():
                raise ValueError("Jede Teilleistung braucht ein Kürzel.")
            if not re.fullmatch(r"[A-Za-z0-9_]+", t["id"]):
                raise ValueError(f"Kürzel {t['id']}: nur Buchstaben, Ziffern und Unterstrich.")
            if t.get("typ") != "liste" and not t.get("zusatz") and not t.get("max"):
                raise ValueError(f"Teilleistung {t['id']} braucht Höchstpunkte.")
        C.validate_profile(prof)
        ziel = w / C.PROFILDIR / f"{pid}.json"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
        CACHE.clear()
        return {"ok": True, "id": pid}
    if name == "quellen":
        from types import SimpleNamespace
        from .pipeline import load_tables
        k = _im_eingang(Path(d["pfad"]))
        tabs = load_tables(k, SimpleNamespace(warnungen=[]))
        out = []
        for t in tabs:
            out.append({"tabelle": t.label, "blatt": t.sheet or "",
                        "spalten": [(f"{t.sheet}:{h}" if t.sheet else h) for h in t.headers]})
        return out
    if name == "ablage":
        k = _im_eingang(Path(d["pfad"]))
        with LOCK:
            dec = C.decisions_for(k)
            dec.answer(qid("ablage", d["key"]), "ja" if d.get("ablegen", True) else "nein",
                       frage=f"Ablage für Matrikelnummer {d['key']}", kategorie=PERSON)
            dec.save()
        return verarbeiten(str(k))
    if name == "manuell":
        k = _im_eingang(Path(d["pfad"]))
        with LOCK:
            dec = C.decisions_for(k)
            wert = str(d.get("wert", "")).strip().replace(",", ".")
            if wert == "":
                dec.answers.pop(qid("manuell", d["key"], d["tid"]), None)
            else:
                float(wert)
                dec.answer(qid("manuell", d["key"], d["tid"]), wert, frage=f"Punkte {d['tid']} für {d['key']} (Eingabe in der App)",
                           kategorie=PERSON)
            dec.save()
        return verarbeiten(str(k))
    if name == "entwurf":
        from . import report as R
        erg = _erg(d["pfad"])
        m = next(x for x in R.entwuerfe(erg, pruefen=False) if x["student"].key == d["key"])
        return {"html": m["html"], "bearbeitet": m["bearbeitet"], "veraltet": m["veraltet"]}
    if name == "entwurf_status":
        from . import report as R
        erg = _erg(d["pfad"])
        try:
            ms = R.entwuerfe(erg, pruefen=False)
        except Exception:  # noqa: BLE001
            return {"bearbeitet": [], "veraltet": []}
        return {"bearbeitet": [m["student"].key for m in ms if m["bearbeitet"]],
                "veraltet": [m["student"].key for m in ms if m["veraltet"]]}
    if name == "entwurf_speichern":
        from . import report as R
        erg = _erg(d["pfad"])
        if d.get("html") is None:
            R.speichere_bearbeitung(erg.kurs_dir, d["key"], None, None)
        else:
            r = erg.ergebnisse[d["key"]]
            basis, _ = R.render(R.vorlage_pfad(erg), R.werte(erg, r))
            R.speichere_bearbeitung(erg.kurs_dir, d["key"], basis, d["html"])
        return {"ok": True}
    if name == "semester_uebersicht":
        sem = _im_eingang(Path(d["pfad"]))
        kurse = []
        for k in sorted(p for p in sem.iterdir() if p.is_dir() and C.is_course_dir(p)):
            aus = k / C.APPDIR / "ausgabe"
            dateien = [p for p in aus.rglob("*") if p.is_file()] if aus.exists() else []
            dec = C.decisions_for(k)
            kurse.append({"name": k.name, "ergebnisdateien": len(dateien),
                          "entscheidungen_person": sum(1 for v in dec.answers.values() if v.get("kategorie") != "struktur"),
                          "originale": len([p for p in k.iterdir() if p.is_file()])})
        return {"semester": sem.name, "abgeschlossen": abgeschlossen(sem), "kurse": kurse,
                "semesterdateien": len([p for p in sem.iterdir() if p.is_file() and p.name != MARKE])}
    if name == "semester_archivieren":
        import zipfile
        from datetime import datetime
        sem = _im_eingang(Path(d["pfad"]))
        ziel_ordner = Path(d["ziel"]).expanduser() if d.get("ziel") else sem
        if not ziel_ordner.is_dir():
            raise FileNotFoundError(f"Zielordner {ziel_ordner} existiert nicht.")
        ziel = ziel_ordner / f"Archiv_{sem.name}_{datetime.now():%Y-%m-%d_%H%M}.zip"
        n = 0
        with LOCK, zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(sem.rglob("*")):
                if not p.is_file() or p == ziel or p.suffix.lower() == ".zip":
                    continue
                rel = p.relative_to(sem)
                in_app = C.APPDIR in rel.parts
                if in_app or d.get("originale"):
                    z.write(p, str(Path(sem.name) / rel))
                    n += 1
        return {"datei": str(ziel), "anzahl": n}
    if name == "semester_abschliessen":
        import shutil
        from datetime import datetime
        sem = _im_eingang(Path(d["pfad"]))
        with LOCK:
            if d.get("loeschen"):
                for k in (p for p in sem.iterdir() if p.is_dir() and C.is_course_dir(p)):
                    aus = k / C.APPDIR / "ausgabe"
                    if aus.exists():
                        shutil.rmtree(aus)
                    b = k / C.APPDIR / "entwurf_bearbeitet.json"
                    if b.exists():
                        b.unlink()
                    dec = C.decisions_for(k)
                    dec.answers = dec.structural_only()      # Zuordnungen bleiben für die Kopie ins nächste Semester
                    dec.save()
                    CACHE.pop(str(k), None)
            (sem / MARKE).write_text(json.dumps({"zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
                                                 "ergebnisse_geloescht": bool(d.get("loeschen"))}), encoding="utf-8")
            for k in list(CACHE):
                if Path(k).parent == sem:
                    CACHE.pop(k, None)
        return zustand()
    if name == "semester_oeffnen":
        sem = _im_eingang(Path(d["pfad"]))
        if (sem / MARKE).exists():
            (sem / MARKE).unlink()
        return zustand()
    if name == "app_einstellungen":
        st = settings()
        if "ueberwachen" in d:
            st["ueberwachen"] = bool(d["ueberwachen"])
        if "autostart" in d:
            setze_autostart(bool(d["autostart"]))
            st["autostart"] = bool(d["autostart"])
        save_settings(st)
        return zustand()
    if name == "profil_kopieren":
        return {"pfad": str(C.copy_profile(wurzel(), d["alt"], d["neu"]))}
    if name == "ordner_waehlen":
        return {"pfad": ordner_dialog()}
    if name == "puls":
        import time
        LETZTER_PULS["t"] = time.time()
        return {"ok": True}
    if name == "aenderung":
        k = _im_eingang(Path(d["pfad"]))
        return {"stand": _stand(k)}
    raise ValueError(f"Unbekannte Aktion {name}")


def hochladen(pfad: str, ziel: str, name: str, daten: bytes, ersetzen: bool) -> dict:
    """Legt eine hochgeladene Datei im LV-Ordner oder (Logbuch) im Semesterordner ab."""
    from .io_tables import SUPPORTED
    k = _im_eingang(Path(pfad))
    ordner = k.parent if ziel == "semester" else k
    name = Path(name.replace("\\", "/")).name
    if not name or name.startswith((".", "~$")) or Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError(f"{name}: Dateityp wird nicht unterstützt (möglich: CSV, XLSX, XML).")
    p = ordner / name
    if p.exists() and p.read_bytes() == daten:
        return {"status": "unveraendert", "name": name}
    if p.exists() and not ersetzen:
        return {"status": "vorhanden", "name": name}
    p.write_bytes(daten)
    CACHE.pop(str(k), None)
    return {"status": "gespeichert", "name": name}


def vorschau(pfad: str, key: str) -> str:
    from . import report as R
    erg = _erg(pfad)
    r = erg.ergebnisse[key]
    h, _ = R.render(R.vorlage_pfad(erg), R.werte(erg, r))
    return h


# --------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def _ok_token(self, qs=None) -> bool:
        tok = self.headers.get("X-Token") or (qs or {}).get("t", [""])[0]
        return secrets.compare_digest(tok, TOKEN)

    def do_GET(self):  # noqa: N802
        SEITENABRUFE["n"] += 1
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/":
            if not self._ok_token(qs):
                return self._send(403, b"Zugriff verweigert", "text/plain; charset=utf-8")
            html = UI.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if not self._ok_token(qs):
            return self._json(403, {"fehler": "Zugriff verweigert"})
        try:
            if u.path == "/api/zustand":
                return self._json(200, zustand())
            if u.path == "/api/vorschau":
                return self._send(200, vorschau(qs["pfad"][0], qs["key"][0]).encode("utf-8"), "text/html; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            return self._json(400, {"fehler": str(e)})
        self._json(404, {"fehler": "nicht gefunden"})

    def do_POST(self):  # noqa: N802
        if not self._ok_token():
            return self._json(403, {"fehler": "Zugriff verweigert"})
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            if u.path == "/api/hochladen":
                if n > 100 * 1024 * 1024:
                    raise ValueError("Datei ist größer als 100 MB.")
                qs = parse_qs(u.query)
                return self._json(200, hochladen(qs["pfad"][0], qs.get("ziel", ["kurs"])[0], qs["name"][0],
                                                 self.rfile.read(n), qs.get("ersetzen", ["0"])[0] == "1"))
            d = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/beenden":
                self._json(200, {"ok": True})
                threading.Thread(target=lambda: os._exit(0), daemon=True).start()
                return
            if u.path.startswith("/api/"):
                return self._json(200, aktion(u.path[5:], d))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json(400, {"fehler": str(e)})
        self._json(404, {"fehler": "nicht gefunden"})


def start_server(port=0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/?t={TOKEN}"


def setze_autostart(an: bool):
    """Trägt die App für den Windows-Start ein (im Hintergrund, ohne Fenster) oder entfernt den Eintrag."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise RuntimeError("Der Start mit Windows lässt sich nur in der Windows-App (LV-Tool.exe) einstellen.")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if an:
            winreg.SetValueEx(k, "LV-Tool", 0, winreg.REG_SZ, f'"{sys.executable}" --hintergrund')
        else:
            try:
                winreg.DeleteValue(k, "LV-Tool")
            except FileNotFoundError:
                pass


STAENDE: dict[str, float] = {}


def _stand(k: Path) -> float:
    dateien = [p for base in (k, k.parent) for p in base.iterdir() if p.is_file() and not p.name.startswith("~$")]
    return max([p.stat().st_mtime for p in dateien] + [(k / C.APPDIR / "kurs.json").stat().st_mtime])


def ueberwache_einmal() -> list[str]:
    """Verarbeitet alle Lehrveranstaltungen offener Semester, deren Dateien sich geändert haben."""
    w = wurzel()
    neu = []
    if not w or not w.exists() or not settings().get("ueberwachen", True):
        return neu
    for sem in (p for p in w.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        if abgeschlossen(sem):
            continue
        for k in (p for p in sem.iterdir() if p.is_dir() and C.is_course_dir(p)):
            try:
                st = _stand(k)
                if STAENDE.get(str(k)) != st:
                    STAENDE[str(k)] = st
                    verarbeiten(str(k))
                    neu.append(str(k))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
    return neu


def _ueberwacher(intervall=30):
    import time
    while True:
        try:
            ueberwache_einmal()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(intervall)


def chrome_pfad() -> str | None:
    """Sucht Google Chrome (kein Edge)."""
    kandidaten = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                        kandidaten.append(winreg.QueryValue(k, None))
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(env):
                kandidaten.append(os.path.join(os.environ[env], "Google", "Chrome", "Application", "chrome.exe"))
    elif sys.platform == "darwin":
        kandidaten.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        import shutil
        kandidaten += [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    return next((k for k in kandidaten if k and os.path.exists(k)), None)


def ordner_dialog() -> str:
    """Systemdialog zur Ordnerauswahl (Tkinter ist in der Windows-App enthalten)."""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(parent=root, title="Eingangsordner wählen") or ""
    finally:
        root.destroy()


LETZTER_PULS = {"t": None}


def _waechter(minuten=3):
    """Beendet die App, wenn das Fenster länger nicht mehr antwortet (nur im Browser-Ersatzmodus)."""
    import time
    while True:
        time.sleep(15)
        t = LETZTER_PULS["t"]
        if t and time.time() - t > minuten * 60 and not settings().get("autostart"):
            os._exit(0)


def _instanz_datei() -> Path:
    return _settings_path().with_name("instanz.json")


def _laufende_instanz() -> str | None:
    """URL einer bereits laufenden App (zweiter Doppelklick öffnet dann nur ein Fenster)."""
    import urllib.request
    try:
        url = json.loads(_instanz_datei().read_text(encoding="utf-8"))["url"]
        base, tok = url.split("/?t=")
        req = urllib.request.Request(base + "/api/zustand", headers={"X-Token": tok})
        with urllib.request.urlopen(req, timeout=2) as r:
            return url if r.status == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _chrome_fenster(chrome: str, url: str):
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "lvtool" / "chrome-fenster"
    base.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([chrome, f"--app={url}", f"--user-data-dir={base}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1360,880"])


def selbsttest(ziel: str) -> int:
    """Prüft die gebaute App auf dem Zielsystem (wird beim Bauen der .exe ausgeführt)."""
    import tempfile
    import time
    import urllib.request
    erg: dict = {"plattform": sys.platform, "eingefroren": bool(getattr(sys, "frozen", False))}
    try:
        tmp = Path(tempfile.mkdtemp(prefix="lvtool_test_"))
        os.environ["APPDATA"] = str(tmp / "_appdata")
        from .beispiel import erzeuge
        k = erzeuge(tmp / "eingang")
        aktion("wurzel", {"pfad": str(tmp / "eingang")})
        e = aktion("verarbeiten", {"pfad": str(k)})
        for _ in range(10):
            if not e["offen"]:
                break
            for q in e["offen"]:
                wert = "nein" if q["frage"].startswith("Termin 2025-09-30") else q["optionen"][0][0]
                aktion("antwort", {"pfad": str(k), "id": q["id"], "wert": wert})
            e = aktion("verarbeiten", {"pfad": str(k)})
        erg["studenten"], erg["offen"] = len(e["studenten"]), len(e["offen"])
        erg["entwuerfe"] = aktion("entwuerfe", {"pfad": str(k), "art": "datei"})["eml"]
        erg["masterliste"] = Path(e["master"]).exists()
        erg["oberflaeche"] = UI.exists()
        try:
            import tkinter  # noqa: F401
            erg["ordnerdialog_modul"] = True
        except Exception as ex:  # noqa: BLE001
            erg["ordnerdialog_modul"] = str(ex)
        if sys.platform == "win32":
            try:
                import pythoncom  # noqa: F401
                import win32com.client  # noqa: F401
                erg["outlook_modul"] = True
            except Exception as ex:  # noqa: BLE001
                erg["outlook_modul"] = str(ex)
        srv, url = start_server()
        with urllib.request.urlopen(url, timeout=5) as r:
            erg["server"] = r.status
        chrome = chrome_pfad()
        erg["chrome"] = chrome or ""
        if chrome:
            SEITENABRUFE["n"] = 0
            proc = _chrome_fenster(chrome, url)
            for _ in range(40):
                time.sleep(0.5)
                if SEITENABRUFE["n"] >= 2:      # Seite und erster Datenabruf aus dem Chrome-Fenster
                    break
            erg["chrome_fenster_laedt_app"] = SEITENABRUFE["n"] >= 2
            erg["chrome_prozess_laeuft"] = proc.poll() is None
            proc.terminate()
        erg["ok"] = bool(erg["studenten"] == 8 and erg["offen"] == 0 and erg["entwuerfe"] == 8 and erg["server"] == 200
                         and erg["oberflaeche"] and erg["masterliste"])
        if sys.platform == "win32":
            erg["ok"] = bool(erg["ok"] and erg["ordnerdialog_modul"] is True and erg["outlook_modul"] is True
                             and (not chrome or erg["chrome_fenster_laedt_app"]))
    except Exception:  # noqa: BLE001
        erg["ok"] = False
        erg["fehler"] = traceback.format_exc()
    Path(ziel).write_text(json.dumps(erg, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if erg["ok"] else 1


SEITENABRUFE = {"n": 0}


def main():
    import time
    if "--selbsttest" in sys.argv:
        sys.exit(selbsttest(sys.argv[sys.argv.index("--selbsttest") + 1]))
    hintergrund = "--hintergrund" in sys.argv
    chrome = None if "--browser" in sys.argv else chrome_pfad()
    vorhanden = _laufende_instanz()
    if vorhanden:
        if hintergrund:
            return
        if chrome:
            _chrome_fenster(chrome, vorhanden)
        else:
            import webbrowser
            webbrowser.open(vorhanden)
        return
    srv, url = start_server()
    try:
        _instanz_datei().parent.mkdir(parents=True, exist_ok=True)
        _instanz_datei().write_text(json.dumps({"url": url}), encoding="utf-8")
    except OSError:
        pass
    threading.Thread(target=_ueberwacher, daemon=True).start()
    if hintergrund:
        threading.Event().wait()      # ohne Fenster: nur überwachen, bis die App beendet wird
    start = time.time()
    if chrome:
        proc = _chrome_fenster(chrome, url)
        proc.wait()
        if settings().get("autostart"):
            threading.Event().wait()  # läuft im Hintergrund weiter; Ende über „App beenden“
        if time.time() - start > 15:
            return      # eigenes Chrome-Fenster wurde geschlossen -> App beenden
        # Chrome hat den Start an einen anderen Prozess abgegeben: über Lebenszeichen des Fensters beenden
    else:
        import webbrowser
        webbrowser.open(url)
        print(f"Google Chrome nicht gefunden; LV-Tool im Standardbrowser geöffnet: {url}", flush=True)
    LETZTER_PULS["t"] = time.time() + 60
    threading.Thread(target=_waechter, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
