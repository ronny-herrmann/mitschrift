"""Text-Normalisierung für den WER-Vergleich (Deutsch).

Ziel: Fehler zählen, die für ein Protokoll relevant sind – nicht Groß-/Kleinschreibung,
Satzzeichen oder Schreibvarianten wie "ß"/"ss". Hypothese und Referenz werden identisch
normalisiert, dadurch ist der Vergleich fair.
"""

from __future__ import annotations

import re
import unicodedata

_NUM_WORDS = {
    "null": "0", "eins": "1", "ein": "1", "eine": "1", "zwei": "2", "drei": "3", "vier": "4", "fünf": "5",
    "sechs": "6", "sieben": "7", "acht": "8", "neun": "9", "zehn": "10", "elf": "11", "zwölf": "12",
}
_FILLERS = {"äh", "ähm", "hm", "mhm", "öhm", "ähh"}


def normalize_de(text: str) -> str:
    t = unicodedata.normalize("NFC", text).lower()
    t = t.replace("ß", "ss")
    t = re.sub(r"[‘’‚“”„`´']", "", t)   # Anführungszeichen/Apostrophe weg
    t = re.sub(r"(\d)[.,](\d)", r"\1\2", t)                        # 1.000 / 3,5 → 1000 / 35 (einheitlich)
    t = re.sub(r"[^\w\s]", " ", t)                                  # Satzzeichen → Leerzeichen
    t = re.sub(r"_", " ", t)
    words = [w for w in t.split() if w not in _FILLERS]
    words = [_NUM_WORDS.get(w, w) for w in words]
    return " ".join(words)
