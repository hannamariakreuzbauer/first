"""Klärungsfragen und gespeicherte Entscheidungen der Benutzerin.

Jede Frage hat eine stabile ID. Ist sie beantwortet, wird die Antwort bei jedem
weiteren Lauf wiederverwendet. Kategorie "struktur" (Spalten, Dateirollen, Codes)
wird beim Kopieren einer Lehrveranstaltung übernommen, Kategorie "person" nicht.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

STRUKTUR = "struktur"
PERSON = "person"


def qid(*parts) -> str:
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Question:
    id: str
    kategorie: str
    frage: str
    optionen: list[tuple[str, str]]          # (Wert, Beschriftung)
    kontext: dict = field(default_factory=dict)
    freitext: bool = False                   # zusätzlich eigene Eingabe erlaubt (z. B. Zahl)


class Decisions:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.answers: dict[str, dict] = {}
        self.open: dict[str, Question] = {}
        if self.path and self.path.exists():
            self.answers = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, id_: str):
        a = self.answers.get(id_)
        return a["antwort"] if a else None

    def ask(self, id_, kategorie, frage, optionen, kontext=None, freitext=False):
        """Liefert die gespeicherte Antwort oder None und merkt die Frage als offen vor."""
        if id_ in self.answers:
            return self.answers[id_]["antwort"]
        self.open[id_] = Question(id_, kategorie, frage, list(optionen), kontext or {}, freitext)
        return None

    def answer(self, id_: str, value, frage: str | None = None, kategorie: str | None = None):
        q = self.open.pop(id_, None)
        self.answers[id_] = {
            "antwort": value,
            "frage": frage or (q.frage if q else ""),
            "kategorie": kategorie or (q.kategorie if q else PERSON),
            "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        }

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.answers, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def structural_only(self) -> dict:
        return {k: v for k, v in self.answers.items() if v.get("kategorie") == STRUKTUR}

    def open_list(self) -> list[dict]:
        return [asdict(q) for q in self.open.values()]
