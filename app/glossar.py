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
