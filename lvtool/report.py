"""Individuelle Reports als E-Mail-Inhalt: Word-Vorlage mit {{PLATZHALTERN}} -> HTML/Text,
Ausgabe als Outlook-Entwürfe, .eml-Entwürfe und Serienbrief-Datei. Es wird nichts versendet."""
from __future__ import annotations

import csv
import html
import re
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import docx
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

from .pipeline import Ergebnis, StudentErgebnis, fmt

PH = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


# --------------------------------------------------------------------------- Platzhalterwerte

def werte(erg: Ergebnis, r: StudentErgebnis) -> dict[str, str]:
    s, cfg = r.student, erg.cfg
    termine = erg.termine + erg.termine_spaeter
    anw = erg.anw.get(s.key, {})
    mia = erg.mia.get(s.key, {})
    a_det = []
    for t in termine:
        if t in erg.termine_spaeter:
            a_det.append(f"{t.label}: ausstehend")
        else:
            a_det.append(f"{t.label}: {'1 (verspätet/vorzeitig)' if anw.get(t.datum) == 'S' else (1 if t.datum in anw else 0)}")
    m_det = [f"{t.label}: {mia.get(t.datum, 0)}" for t in termine if t not in erg.termine_spaeter]
    v = {
        "LV_TITEL": cfg.get("lv_titel") or cfg.get("bezeichnung", ""),
        "SEMESTER": erg.kurs_dir.parent.name,
        "NACHNAME": s.nachname, "VORNAME": s.vorname, "MATRIKEL": s.matrikel, "EMAIL": s.email or "",
        "STUDIUM": str(s.felder.get("studium") or ""), "ANREDE": str(s.felder.get("anrede") or ""),
        "SUMME": fmt(r.summe), "NOTE": r.note, "STATUS": r.status,
        "HINWEISE": "\n".join(r.hinweise),
        "ANWESENHEIT_DETAILS": "\n".join(a_det), "ANWESENHEIT_SUMME": str(r.anwesend),
        "FEHLTERMINE": str(r.abwesend), "TERMINE_ANZAHL": str(len(erg.termine)),
        "ANWESENHEITSPFLICHT": r.pflicht,
        "MITARBEIT_DETAILS": "\n".join(m_det), "MITARBEIT_SUMME": str(r.mitarbeit),
        "RUECKMELDEFRIST": cfg.get("rueckmeldefrist", ""), "ABSENDER_NAME": cfg.get("absender_name", ""),
    }
    for tid, te in r.teile.items():
        if te.wert is not None:
            v[tid] = fmt(te.wert)
        else:
            v[tid] = "–" if te.zusatz else ("fehlt" if te.status == "fehlt" else "offen")
        v[f"MAX_{tid}"] = fmt(te.max)
    for k, txt in r.texte.items():
        v[k] = txt
    return v


def platzhalter_der_vorlage(vorlage: Path) -> set[str]:
    d = docx.Document(str(vorlage))
    found = set()
    for p in _alle_absaetze(d):
        found |= set(PH.findall(p.text))
    return found


def _alle_absaetze(d):
    for p in d.paragraphs:
        yield p
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                yield from c.paragraphs


# --------------------------------------------------------------------------- Rendering

def _ersetze(text: str, v: dict) -> str:
    return PH.sub(lambda m: v.get(m.group(1), m.group(0)), text)


def _runs_html(p: Paragraph, v: dict) -> str:
    voll = p.text
    if "{{" in voll and any("{{" in r.text and "}}" not in r.text for r in p.runs):
        # Platzhalter über mehrere Formatierungsabschnitte verteilt: als Ganzes ersetzen
        return html.escape(_ersetze(voll, v)).replace("\n", "<br>")
    out = []
    for r in p.runs:
        t = html.escape(_ersetze(r.text, v)).replace("\n", "<br>").replace("\t", "&emsp;")
        if not t:
            continue
        if r.bold:
            t = f"<strong>{t}</strong>"
        if r.italic:
            t = f"<em>{t}</em>"
        out.append(t)
    return "".join(out)


def _style(p: Paragraph) -> str:
    return (p.style.name if p.style is not None else "").lower()


def render(vorlage: Path, v: dict) -> tuple[str, str]:
    """Liefert (HTML, Text) für einen Studenten."""
    d = docx.Document(str(vorlage))
    body_html, body_txt, liste = [], [], False
    for el in d.element.body.iterchildren():
        tag = el.tag.split("}")[-1]
        if tag == "p":
            p = Paragraph(el, d)
            st = _style(p)
            inhalt = _runs_html(p, v)
            text = _ersetze(p.text, v)
            if PH.search(p.text) and not PH.sub("", p.text).strip() and not text.strip():
                continue  # Absatz nur aus leerem Platzhalter (z. B. keine Hinweise)
            ist_liste = "list" in st or "liste" in st or el.find(".//{*}numPr") is not None
            if ist_liste and not liste:
                body_html.append("<ul>")
                liste = True
            if not ist_liste and liste:
                body_html.append("</ul>")
                liste = False
            if st.startswith(("title", "titel")):
                body_html.append(f"<h1>{inhalt}</h1>")
            elif st.startswith(("heading 1", "überschrift 1")):
                body_html.append(f"<h2>{inhalt}</h2>")
            elif st.startswith(("heading", "überschrift")):
                body_html.append(f"<h3>{inhalt}</h3>")
            elif ist_liste:
                body_html.append(f"<li>{inhalt}</li>")
            else:
                body_html.append(f"<p>{inhalt or '&nbsp;'}</p>")
            body_txt.append(("• " if ist_liste else "") + text)
        elif tag == "tbl":
            if liste:
                body_html.append("</ul>")
                liste = False
            t = DocxTable(el, d)
            rows_h, rows_t = [], []
            for row in t.rows:
                zellen, gesehen = [], set()
                for c in row.cells:
                    if id(c._tc) in gesehen:
                        continue
                    gesehen.add(id(c._tc))
                    zellen.append(c)
                inh = ["<br>".join(_runs_html(p, v) for p in c.paragraphs) for c in zellen]
                if len(zellen) == 1:
                    rows_h.append(f"<tr><td colspan='2'><strong>{inh[0]}</strong></td></tr>")
                else:
                    rows_h.append("<tr>" + "".join(f"<td style='padding:2px 12px 2px 0;vertical-align:top;font-size:11pt'>{x}</td>" for x in inh) + "</tr>")
                rows_t.append("\t".join(_ersetze(c.text, v) for c in zellen))
            body_html.append("<table style='border-collapse:collapse'>" + "".join(rows_h) + "</table>")
            body_txt.extend(rows_t)
    if liste:
        body_html.append("</ul>")
    html_doc = ("<html><head><meta charset='utf-8'></head><body style=\"font-family:Calibri,Arial,sans-serif;font-size:11pt\">"
                + "\n".join(body_html) + "</body></html>")
    return html_doc, "\n".join(body_txt)


# --------------------------------------------------------------------------- Ausgabe

def vorlage_pfad(erg: Ergebnis) -> Path:
    name = erg.cfg.get("vorlage")
    if not name:
        raise FileNotFoundError("In kurs.json ist keine Report-Vorlage („vorlage“) eingetragen.")
    for base in (erg.kurs_dir / "_lvtool", erg.kurs_dir, erg.kurs_dir.parent.parent / "_vorlagen"):
        if (base / name).exists():
            return base / name
    raise FileNotFoundError(f"Report-Vorlage {name} nicht gefunden.")


def entwuerfe(erg: Ergebnis) -> list[dict]:
    vorlage = vorlage_pfad(erg)
    bekannte = set(werte(erg, next(iter(erg.ergebnisse.values()))).keys()) if erg.ergebnisse else set()
    unbekannt = platzhalter_der_vorlage(vorlage) - bekannte
    if unbekannt:
        raise ValueError("Unbekannte Platzhalter in der Vorlage: " + ", ".join(sorted(unbekannt)))
    out = []
    for s in erg.students:
        r = erg.ergebnisse[s.key]
        v = werte(erg, r)
        h, t = render(vorlage, v)
        betreff = re.sub(r"\{\{?\s*([A-Za-z0-9_]+)\s*\}\}?", lambda m: v.get(m.group(1), m.group(0)), erg.cfg.get("betreff", ""))
        out.append({"student": s, "ergebnis": r, "an": s.email, "betreff": betreff, "html": h, "text": t, "werte": v})
    return out


def _dateiname(s) -> str:
    return re.sub(r"[^\w\-]+", "_", f"{s.nachname}_{s.vorname}_{s.matrikel}", flags=re.UNICODE)


def schreibe_eml(mails: list[dict], ziel: Path, absender: str = "") -> list[Path]:
    ziel.mkdir(parents=True, exist_ok=True)
    out = []
    for m in mails:
        msg = EmailMessage()
        if absender:
            msg["From"] = absender
        if m["an"]:
            msg["To"] = formataddr((f"{m['student'].vorname} {m['student'].nachname}", m["an"]))
        msg["Subject"] = m["betreff"]
        msg["X-Unsent"] = "1"   # öffnet in Outlook als Entwurf
        msg.set_content(m["text"])
        msg.add_alternative(m["html"], subtype="html")
        p = ziel / f"{_dateiname(m['student'])}.eml"
        p.write_bytes(bytes(msg))
        out.append(p)
    return out


def schreibe_serienbrief(mails: list[dict], ziel: Path) -> tuple[Path, Path]:
    from openpyxl import Workbook
    ziel.mkdir(parents=True, exist_ok=True)
    keys = list(mails[0]["werte"].keys()) if mails else []
    header = ["E_MAIL_AN", "BETREFF"] + keys + ["TEXT"]
    rows = [[m["an"] or "", m["betreff"]] + [m["werte"][k] for k in keys] + [m["text"]] for m in mails]
    wb = Workbook()
    ws = wb.active
    ws.title = "Serienbrief"
    ws.append(header)
    for r in rows:
        ws.append(r)
    x = ziel / "serienbrief.xlsx"
    wb.save(x)
    c = ziel / "serienbrief.csv"
    with c.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
    return x, c


def outlook_entwuerfe(mails: list[dict], konto: str = "") -> int:
    """Legt Entwürfe im klassischen Outlook (Windows) an. Benötigt pywin32."""
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise RuntimeError("Outlook-Anbindung braucht Windows, klassisches Outlook und das Paket pywin32 "
                           "(pip install pywin32).") from e
    ol = win32com.client.Dispatch("Outlook.Application")
    ns = ol.GetNamespace("MAPI")
    ordner = None
    if konto:
        for acc in ns.Accounts:
            if str(acc.SmtpAddress).lower() == konto.lower():
                ordner = acc.DeliveryStore.GetDefaultFolder(16)  # olFolderDrafts
                break
        if ordner is None:
            raise RuntimeError(f"Outlook-Konto {konto} nicht gefunden.")
    else:
        ordner = ns.GetDefaultFolder(16)
    n = 0
    for m in mails:
        item = ordner.Items.Add(0)  # olMailItem
        if m["an"]:
            item.To = m["an"]
        item.Subject = m["betreff"]
        item.HTMLBody = m["html"]
        item.Save()
        n += 1
    return n


# --------------------------------------------------------------------------- Vorlage JAT

def erstelle_jat_vorlage(ziel: Path):
    """Vorlage nach dem Muster von 02_report_jat_abrechnung.docx, mit Platzhaltern statt Werten."""
    d = docx.Document()
    d.add_paragraph("{{LV_TITEL}}: Student Report", style="Title")
    d.add_paragraph("{{NACHNAME}}, {{VORNAME}}")
    d.add_paragraph("")
    d.add_paragraph("Hallo,")
    d.add_paragraph("soeben habe ich eure Student Reports (insb. mit der für die gesamte LV geltenden Gesamtzahl von "
                    "Notenpunkten [= NP] und meinem darauf basierenden individuellen Benotungsvorschlag) fertiggestellt. "
                    "Ihr findet das alles weiter unten im Mail.")
    d.add_paragraph("Wie sich die Gesamtnote aus den (insgesamt 100) NP ergibt, kann man anhand der Grenzwerte leicht nachrechnen:")
    for a, b in (("ab 87,5 NP: ", "1 (SG)"), ("ab 75 NP: ", "2 (GU)"), ("ab 62,5 NP: ", "3 (BE)"), ("ab 50 NP: ", "4 (GN)"),
                 ("unter 50 NP oder mindestens eine fehlende bzw. nicht genügende Teilleistung: ", "5 (NG)")):
        p = d.add_paragraph(style="List Bullet")
        p.add_run(a)
        p.add_run(b).bold = True
    d.add_paragraph("")
    d.add_paragraph("Bitte beachtet, dass ich mir knappe Ergebnisse nicht ansehe und das natürlich auch nicht berücksichtige. "
                    "Ferner, dass alles bilateral Vereinbarte durch diesen Report selbstverständlich nicht außer Kraft gesetzt wird "
                    "und dass irrtümliche Eintragungen keinerlei Ansprüche begründen. Da die Gesamtabrechnung etwas kompliziert war, "
                    "kann es sein, dass mir da und dort ein Fehler unterlaufen ist. Das ist keine Absicht, und daher bitte ich euch, "
                    "alles genau zu kontrollieren und euch gegebenenfalls gerne bei mir zu melden. Insbesondere gibt es einige "
                    "überraschende NG wegen Abwesenheiten. In diesem Fall bitte melden, denn vielleicht lässt sich das ja noch aufklären.")
    d.add_paragraph("")
    p = d.add_paragraph()
    p.add_run("Wichtige Termine!").bold = True
    p = d.add_paragraph(style="List Bullet")
    p.add_run("Für alle: ").bold = True
    p.add_run("Bis {{RUECKMELDEFRIST}}, 24:00 Uhr, läuft jetzt die „Rechtsmittelfrist“, innerhalb derer noch Gelegenheit ist, "
              "über die Noten zu sprechen. Bitte gerne mailen, wenn es aus eurer Sicht offene Punkte gibt.")
    p = d.add_paragraph(style="List Bullet")
    p.add_run("Für Verbesserungswillige ").bold = True
    p.add_run("(gilt nicht für bereits abschließend Beurteilte, insb. auch nicht für 5 (NG), mit denen ich bereits bilateral "
              "gesprochen habe): Bitte ebenfalls innerhalb der „Rechtsmittelfrist“ mailen, damit wir etwaige Kompensations- "
              "oder Verbesserungsmöglichkeiten besprechen können.")
    d.add_paragraph("Für alle anderen, die ich um ein Mail bzw. eine Meldung ersuche: Bitte ebenfalls so schnell wie möglich "
                    "bei mir melden.", style="List Bullet")
    d.add_paragraph("")
    d.add_paragraph("Vielen Dank für die gute Zusammenarbeit und die sehr nette und lustige Atmosphäre! Ich finde, wir können mit "
                    "dem Ergebnis der LV sehr zufrieden sein, und ich wünsche euch allen alles Gute für den weiteren Verlauf "
                    "eures Studiums und/oder Lebens!")
    d.add_paragraph("")
    d.add_paragraph("{{ABSENDER_NAME}}")
    d.add_heading("Ergebnisse", level=1)
    d.add_heading("Das Wichtigste in Kürze", level=2)
    t = d.add_table(rows=0, cols=2)
    for a, b in (("{{NACHNAME}}, {{VORNAME}}", ""), ("Matrikelnummer:", "{{MATRIKEL}}"), ("Studium:", "{{STUDIUM}}"),
                 ("E-Mail:", "{{EMAIL}}"), ("Gesamtpunkte:", "{{SUMME}}"), ("Note:", "{{NOTE}}"),
                 ("Status:", "{{STATUS}}"), ("Kommentar:", "{{KOMMENTAR}}")):
        c = t.add_row().cells
        c[0].text, c[1].text = a, b
    d.add_paragraph("{{HINWEISE}}")
    d.add_heading("Details zur Gesamtbeurteilung", level=2)
    t = d.add_table(rows=0, cols=2)
    for a, b in (("Test (30):", "{{TEST_30}}"), ("HUE 1 (40):", "{{HUE_1_40}}"), ("AW-Punkte (10):", "{{AW_SCORE_10}}"),
                 ("Mitarbeit (10):", "{{MIA_ALL_10}}"), ("Rechercheübung (10):", "{{RECH_10}}"),
                 ("Sonderpunkte (positiv oder negativ):", "{{EXTRA_SCORE}}"), ("Gesamt:", "{{SUMME}}")):
        c = t.add_row().cells
        c[0].text, c[1].text = a, b
    d.add_heading("Details zu Anwesenheit und Mitarbeit in den einzelnen Stunden", level=2)
    d.add_heading("Anwesenheit", level=3)
    d.add_paragraph("Man beachte, dass es sich bei diesen Werten um keine Notenpunkte handelt.")
    d.add_paragraph("{{ANWESENHEIT_DETAILS}}")
    d.add_paragraph("Gesamt: {{ANWESENHEIT_SUMME}} von {{TERMINE_ANZAHL}} (bzw. {{FEHLTERMINE}} Abwesenheit/-en)")
    d.add_heading("Mitarbeit", level=3)
    d.add_paragraph("{{MITARBEIT_DETAILS}}")
    d.add_paragraph("Gesamt: {{MITARBEIT_SUMME}}")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(ziel))
    return ziel
