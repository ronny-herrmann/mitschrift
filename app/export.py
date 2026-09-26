"""Export: TXT, Markdown, SRT (Untertitel), DOCX (Word)."""

from __future__ import annotations

import io
from datetime import datetime

from .store import Transcript


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
    created = datetime.fromisoformat(t.created_at).strftime("%d.%m.%Y %H:%M") if t.created_at else ""
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


def to_docx(t: Transcript, protokoll_md: str | None = None) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(t.title, level=1)
    created = datetime.fromisoformat(t.created_at).strftime("%d.%m.%Y %H:%M") if t.created_at else ""
    doc.add_paragraph(f"Aufgenommen: {created}   ·   Dauer: {fmt_time(t.duration)}   ·   Spracherkennung: {t.model}")

    if protokoll_md:
        doc.add_heading("Protokoll", level=2)
        for line in protokoll_md.splitlines():
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
                doc.add_paragraph(line[2:], style="List Bullet")
            else:
                doc.add_paragraph(line)
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
