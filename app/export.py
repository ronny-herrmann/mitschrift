"""Export: TXT, Markdown, SRT (Untertitel), DOCX (Word)."""

from __future__ import annotations

import io
from datetime import datetime

from .store import Transcript


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


def to_docx(t: Transcript, protokoll_md: str | None = None, protokoll_name: str = "Zusammenfassung",
            fliesstext: bool = False, nur_protokoll: bool = False, pruefstatus: str = "",
            teilnehmende: list[str] | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(t.title, level=1)
    created = local_dt(t.created_at) if t.created_at else ""
    meta = doc.add_paragraph(f"Aufgenommen: {created}   ·   Dauer: {fmt_time(t.duration)}   ·   Spracherkennung: {t.model}")
    meta.runs[0].font.size = Pt(9)
    meta.runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    if teilnehmende:
        tp = doc.add_paragraph()
        tp.add_run("Teilnehmende: ").bold = True
        tp.add_run(", ".join(teilnehmende))

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
            doc.add_heading(protokoll_name, level=2)
            note = doc.add_paragraph((pruefstatus + ". " if pruefstatus else "")
                                     + "Jede Aussage wurde automatisch gegen das Transkript geprüft.")
            note.runs[0].font.size = Pt(9)
            note.runs[0].italic = True
            for line in strip_refs(protokoll_md).splitlines():
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("### "):
                    doc.add_heading(line[4:], level=4)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=3)
                elif line.startswith("# "):
                    doc.add_heading(line[2:], level=2)
                elif line.startswith(("- ", "* ")):
                    _md_inline(doc.add_paragraph(style="List Bullet"), line[2:])
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
