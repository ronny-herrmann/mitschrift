"""Glossar: Nachkorrektur bekannter Begriffe (Ämter, Gremien, Namen, Fachwörter).

Modelle erkennen Eigennamen nicht immer richtig. Das Glossar ersetzt bekannte
Fehlschreibungen wortgenau (Groß-/Kleinschreibung egal) – einfach, transparent
und ohne Modelltraining. Beispiel: "Heilbrunn" → "Heilbronn".
"""

from __future__ import annotations

import re


def compile_glossar(entries: list[dict]) -> list[tuple[re.Pattern, str]]:
    rules = []
    for e in entries or []:
        von = (e.get("von") or "").strip()
        zu = (e.get("zu") or "").strip()
        if not von or not zu or von.lower() == zu.lower():
            continue
        rules.append((re.compile(r"(?<!\w)" + re.escape(von) + r"(?!\w)", re.IGNORECASE), zu))
    return rules


def apply_glossar(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    for pat, zu in rules:
        text = pat.sub(zu, text)
    return text


# ---------------------------------------------------------------------------
# Import: Glossar aus Excel/CSV, Tagesordnung aus Word/PDF/Text
# ---------------------------------------------------------------------------
_HEAD_VON = ("erkannt", "falsch", "von", "fehl", "gehört", "statt")
_HEAD_ZU = ("richtig", "zu", "korrekt", "schreibweise", "soll")
_HEAD_AMT = ("amt", "bereich", "abteilung")


def _rows_to_entries(rows: list[list[str]]) -> list[dict]:
    rows = [[str(c or "").strip() for c in r] for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        return []
    head = [c.lower() for c in rows[0]]

    def find(keys):
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return None

    iv, iz, ia = find(_HEAD_VON), find(_HEAD_ZU), find(_HEAD_AMT)
    if iv is not None and iz is not None and iv != iz:
        body = rows[1:]
    else:
        iv, iz, ia, body = 0, 1, (2 if len(rows[0]) > 2 else None), rows
    out = []
    for r in body:
        get = lambda i: r[i] if i is not None and i < len(r) else ""  # noqa: E731
        out.append({"von": get(iv), "zu": get(iz), "amt": get(ia)})
    return out


def read_table(filename: str, data: bytes) -> list[dict]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return _rows_to_entries([list(r) for r in ws.iter_rows(values_only=True)])
    if name.endswith((".csv", ".txt")):
        import csv
        import io
        text = data.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=";,\t") if text.strip() else csv.excel
        return _rows_to_entries(list(csv.reader(io.StringIO(text), dialect)))
    raise ValueError("Bitte eine Excel-Datei (.xlsx) oder CSV-Datei hochladen.")


def read_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    import io
    if name.endswith(".docx"):
        from docx import Document
        doc = Document(io.BytesIO(data))
        lines = [p.text for p in doc.paragraphs]
        for tbl in doc.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" ".join(dict.fromkeys(cells)))
        return "\n".join(lines)
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:10])
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8-sig", errors="replace")
    raise ValueError("Bitte eine Word-Datei (.docx), PDF oder Textdatei hochladen.")
