"""Export: TXT, Markdown, SRT (Untertitel), DOCX (Word)."""

from __future__ import annotations

import io
from datetime import datetime

from .store import Transcript


def fmt_dauer(sec: float) -> str:
    """1:09 Min. bzw. 1:02:03 Std. – für Menschen lesbar."""
    sec = int(round(sec or 0))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d} Std." if h else f"{m}:{s:02d} Min."


def local_dt(iso: str) -> str:
    """Zeitstempel (UTC gespeichert) in deutscher Ortszeit anzeigen."""
    from zoneinfo import ZoneInfo
    try:
        return datetime.fromisoformat(iso).astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def fmt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h, rest = divmod(int(sec), 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_srt(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _line(seg, with_time: bool, with_speaker: bool) -> str:
    parts = []
    if with_time:
        parts.append(f"[{fmt_time(seg.start)}]")
    if with_speaker and seg.speaker:
        parts.append(f"{seg.speaker}:")
    parts.append(seg.clean or seg.text)
    return " ".join(parts)


def to_txt(t: Transcript, with_time: bool = True, with_speaker: bool = True) -> str:
    lines = [_line(s, with_time, with_speaker) for s in t.segments]
    return "\n".join(lines) + "\n"


def to_markdown(t: Transcript) -> str:
    created = local_dt(t.created_at) if t.created_at else ""
    out = [f"# {t.title}", "", f"Aufgenommen: {created} · Dauer: {fmt_time(t.duration)} · Modell: {t.model}", ""]
    for s in t.segments:
        out.append(f"- **{fmt_time(s.start)}** {('*' + s.speaker + ':* ') if s.speaker else ''}{s.clean or s.text}")
    return "\n".join(out) + "\n"


def to_srt(t: Transcript) -> str:
    out = []
    for i, s in enumerate(t.segments, 1):
        body = s.clean or s.text
        text = f"{s.speaker}: {body}" if s.speaker else body
        out += [str(i), f"{_fmt_srt(s.start)} --> {_fmt_srt(s.end)}", text, ""]
    return "\n".join(out)


def refs_to_times(md: str, t: Transcript) -> str:
    """[S12] → [03:41] – im Export sind Zeitmarken verständlicher als Segmentnummern."""
    import re
    starts = {s.idx: s.start for s in t.segments}

    def repl(m):
        nums = [int(n) for n in re.findall(r"\d+", m.group(0))]
        times = [fmt_time(starts[n]) for n in nums if n in starts]
        return f"[{', '.join(dict.fromkeys(times))}]" if times else ""
    return re.sub(r"\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]", repl, md)


def strip_refs(md: str) -> str:
    """Belegnummern ([S12]) für Leser entfernen – die Prüfung ist dann schon erfolgt."""
    import re
    md = re.sub(r"\s*\[\s*S\s*\d+(?:\s*[,;–-]\s*S?\s*\d+)*\s*\]", "", md)
    return re.sub(r"[ \t]+\n", "\n", md)


def _md_inline(p, text: str) -> None:
    """**fett** aus dem Protokoll als fette Schrift übernehmen."""
    import re
    parts = re.split(r"(\*\*.+?\*\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            p.add_run(part[2:-2]).bold = True
        elif part:
            p.add_run(part)


VORLAGEN_DIR = __import__("pathlib").Path(__file__).parent / "templates"


def vorlagen() -> list[str]:
    """Verfügbare Word-Briefköpfe (Dateinamen ohne Endung)."""
    return sorted(p.stem for p in VORLAGEN_DIR.glob("*.docx")) if VORLAGEN_DIR.exists() else []


def _sdt(paragraph, placeholder: str, tag: str) -> None:
    """Ausfüllbares Feld (Word-Inhaltssteuerelement) mit grauem Platzhaltertext einfügen."""
    from docx.oxml import parse_xml
    xml = ('<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:sdtPr><w:alias w:val="{placeholder}"/><w:tag w:val="{tag}"/><w:showingPlcHdr/><w:text/></w:sdtPr>'
           f'<w:sdtContent><w:r><w:rPr><w:color w:val="808080"/></w:rPr><w:t xml:space="preserve">{placeholder}</w:t></w:r>'
           '</w:sdtContent></w:sdt>')
    paragraph._p.append(parse_xml(xml))


def _fill_marker(doc, marker: str, text: str | None = None, placeholder: str = "", tag: str = "") -> None:
    """Platzhalter {{…}} in den Tabellen der Vorlage ersetzen – durch Text oder ein ausfüllbares Feld."""
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    if marker not in p.text:
                        continue
                    runs = p.runs
                    if text is not None:
                        for r in runs[1:]:
                            r._r.getparent().remove(r._r)
                        lines = text.split("\n")
                        runs[0].text = lines[0]
                        for extra in lines[1:]:
                            runs[0].add_break()
                            runs[0].add_text(extra)
                    else:
                        for r in runs:
                            r._r.getparent().remove(r._r)
                        _sdt(p, placeholder, tag)


def _base_document(t: Transcript, titel: str, vorlage: str | None):
    """Neues Dokument – mit Heilbronner Briefkopf, wenn eine Vorlage vorhanden ist."""
    from docx import Document
    from docx.shared import Pt
    name = vorlage if vorlage in vorlagen() else (vorlagen()[0] if vorlagen() else None)
    if name:
        doc = Document(str(VORLAGEN_DIR / f"{name}.docx"))
        datum = local_dt(t.created_at)[:10] if t.created_at else ""
        _fill_marker(doc, "{{DATUM}}", datum)
        _fill_marker(doc, "{{TITEL}}", titel)
        _fill_marker(doc, "{{AMT}}", placeholder="Amt eintragen", tag="amt")
        _fill_marker(doc, "{{GZ}}", placeholder="Gz. eintragen", tag="gz")
        _fill_marker(doc, "{{TELEFON}}", placeholder="Telefon eintragen", tag="telefon")
        return doc, True
    doc = Document()
    doc.styles["Normal"].font.name = "Source Sans Pro"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(titel.replace("\n", " – "), level=1)
    return doc, False


def _aufgaben_tabelle(doc, rows: list[list[str]]) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tbl = doc.add_table(rows=1, cols=3)
    try:
        tbl.style = doc.styles["Table Grid"]
    except KeyError:
        pass
    for cell, txt in zip(tbl.rows[0].cells, ("Wer", "Was", "Bis wann")):
        cell.text = ""
        r = cell.paragraphs[0].add_run(txt)
        r.bold = True
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), "F2F2F2")
        cell._tc.get_or_add_tcPr().append(shd)
    for row in rows:
        row = (row + ["", "", ""])[:3]
        cells = tbl.add_row().cells
        for cell, txt in zip(cells, row):
            cell.text = txt if txt and txt not in ("-", "–") else "–"
    from docx.shared import Cm
    for row in tbl.rows:
        for cell, w in zip(row.cells, (Cm(3.5), Cm(9.5), Cm(3))):
            cell.width = w
    doc.add_paragraph()


def _bullet(doc, text: str, briefkopf: bool):
    from docx.shared import Cm
    if briefkopf:
        p = doc.add_paragraph(style="List Paragraph")
        p.paragraph_format.left_indent = Cm(0.6)
        p.paragraph_format.first_line_indent = Cm(-0.4)
        p.add_run("•\u00a0\u00a0")
    else:
        p = doc.add_paragraph(style="List Bullet")
    _md_inline(p, text)
    return p


def to_docx(t: Transcript, protokoll_md: str | None = None, protokoll_name: str = "Zusammenfassung",
            fliesstext: bool = False, nur_protokoll: bool = False, pruefstatus: str = "",
            teilnehmende: list[str] | None = None, vorlage: str | None = None) -> bytes:
    from docx.shared import Cm, Pt, RGBColor

    art = protokoll_name if protokoll_md else ("Fließtext" if fliesstext else "Transkript")
    doc, briefkopf = _base_document(t, f"{art}\n{t.title}", vorlage)

    # Sitzungsdaten als Kopfblock (Protokollführung ist ausfüllbar)
    created = local_dt(t.created_at) if t.created_at else ""
    if briefkopf:
        doc.add_paragraph()  # Abstand zum Titelkasten
    zeilen = [("Sitzung", t.title), ("Datum und Uhrzeit", created), ("Dauer", fmt_dauer(t.duration))]
    if teilnehmende:
        zeilen.append(("Teilnehmende", ", ".join(teilnehmende)))
    for label, wert in zeilen + [("Protokollführung", None)]:
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(Cm(4.5))
        p.paragraph_format.space_after = Pt(2)
        p.add_run(f"{label}:\t").bold = True
        if wert is None:
            _sdt(p, "Name eintragen", "protokollfuehrung")
        else:
            p.add_run(wert)
    hinweis = doc.add_paragraph(f"Automatisch erstellt mit dem Protokollanten der Stadt Heilbronn · Spracherkennung: {t.model}")
    hinweis.runs[0].font.size = Pt(8)
    hinweis.runs[0].font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    if fliesstext:
        # Absätze je Sprecherwechsel, ohne Zeitmarken
        cur_sp, buf = None, []
        for s in t.segments:
            if s.speaker != cur_sp and buf:
                doc.add_paragraph(" ".join(buf))
                buf = []
            cur_sp = s.speaker
            buf.append(s.clean or s.text)
        if buf:
            doc.add_paragraph(" ".join(buf))
    else:
        if protokoll_md:
            if not briefkopf:  # im Briefkopf steht die Art schon im Titelkasten
                doc.add_heading(protokoll_name, level=2)
            note = doc.add_paragraph((pruefstatus + ". " if pruefstatus else "")
                                     + "Jede Aussage wurde automatisch gegen das Transkript geprüft.")
            note.runs[0].font.size = Pt(9)
            note.runs[0].italic = True
            lines = [ln.rstrip() for ln in strip_refs(protokoll_md).splitlines()]
            section, i = "", 0
            while i < len(lines):
                line = lines[i]
                i += 1
                if not line:
                    continue
                # Aufgaben „Wer | Was | Bis wann“ als Tabelle
                if section.lower().startswith("aufgaben") and line.startswith(("- ", "* ")) and line.count("|") >= 2:
                    rows = [line]
                    while i < len(lines) and lines[i].startswith(("- ", "* ")) and lines[i].count("|") >= 2:
                        rows.append(lines[i])
                        i += 1
                    _aufgaben_tabelle(doc, [[c.strip() for c in r[2:].split("|")][:3] for r in rows])
                    continue
                if line.startswith("## "):
                    section = line[3:].strip()
                if line.startswith("### "):
                    doc.add_heading(line[4:], level=4)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=3)
                elif line.startswith("# "):
                    doc.add_heading(line[2:], level=2)
                elif line.startswith(("- ", "* ")):
                    _bullet(doc, line[2:], briefkopf)
                else:
                    _md_inline(doc.add_paragraph(), line)
            if nur_protokoll:
                buf = io.BytesIO()
                doc.save(buf)
                return buf.getvalue()
            doc.add_page_break()

        doc.add_heading("Transkript", level=2)
        for s in t.segments:
            p = doc.add_paragraph()
            r = p.add_run(f"[{fmt_time(s.start)}] ")
            r.font.size = Pt(9)
            if s.speaker:
                r2 = p.add_run(f"{s.speaker}: ")
                r2.bold = True
            p.add_run(s.clean or s.text)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
