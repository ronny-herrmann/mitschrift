"""KI-Bereinigung des Transkripts – korrigieren, ohne zu erfinden.

Was die KI darf: falsch erkannte Wörter korrigieren (z. B. „CHS“ → „Excel“, wenn der
Zusammenhang eindeutig ist oder das Glossar es vorgibt), Rechtschreibung, Zeichensetzung,
Groß-/Kleinschreibung, Verzögerungslaute („äh“) und Stotterer entfernen.
Was sie nicht darf: zusammenfassen, umformulieren, ergänzen, weglassen.

Jede Zeile trägt ihre Segmentnummer ([S12]); die Antwort wird zeilenweise zurückgeordnet
und pro Segment gegen das Original geprüft (Wort-Ähnlichkeit, neue Zahlen). Weicht eine
Zeile zu stark ab, bleibt das Original stehen und die Zeile wird markiert.
"""

from __future__ import annotations

import difflib
import logging
import re

from .store import Segment, Transcript

log = logging.getLogger(__name__)

SYSTEM = (
    "Du bist Korrektor für automatisch erstellte Sprach-Transkripte einer deutschen Stadtverwaltung. "
    "Du korrigierst ausschließlich Erkennungs- und Schreibfehler. Du fasst nie zusammen und ergänzt nie Inhalte."
)

RULES = """Regeln:
- Jede Zeile beginnt mit ihrer Nummer, z. B. [S12]. Gib GENAU die gleichen Zeilen mit den gleichen Nummern in gleicher Reihenfolge zurück – eine Zeile pro Nummer.
- Korrigiere: falsch erkannte Wörter (wenn der Zusammenhang eindeutig ist), Rechtschreibung, Zeichensetzung, Groß-/Kleinschreibung, Zahlen und Datumsangaben in üblicher Schreibweise.
- Entferne: Verzögerungslaute (äh, ähm, hm), direkte Wortwiederholungen und Stotterer.
- Verboten: zusammenfassen, umformulieren, Sätze umstellen, Inhalte ergänzen oder weglassen, Namen/Zahlen erfinden.
- Wenn eine Stelle unklar ist: unverändert lassen.
- Nur die Zeilen ausgeben, keine Erklärungen."""


def glossar_hint(glossar: list[dict]) -> str:
    terms = [e["zu"] for e in glossar or [] if e.get("zu")]
    pairs = [f"{e['von']} → {e['zu']}" for e in glossar or [] if e.get("von") and e.get("zu")]
    if not terms:
        return ""
    return ("Fachbegriffe und Namen, die richtig geschrieben werden müssen: " + ", ".join(dict.fromkeys(terms))
            + ("\nBekannte Fehlerkennungen: " + "; ".join(pairs) if pairs else "") + "\n")


def seg_text(s: Segment) -> str:
    return s.text


def build_lines(segments: list[Segment]) -> str:
    return "\n".join(f"[S{s.idx}] {seg_text(s)}" for s in segments)


def build_prompt(t: Transcript, glossar: list[dict]) -> str:
    """Vollständiger Prompt für NOVA (Zwischenablage)."""
    return (f"Bereinige das folgende Transkript.\n\n{RULES}\n\n{glossar_hint(glossar)}\n"
            f"TRANSKRIPT:\n{build_lines(t.segments)}\n")


_LINE = re.compile(r"^\s*\[\s*S\s*(\d+)\s*\]\s*(.*)$")


def parse_lines(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            # Markdown-Hervorhebungen, die kleine Modelle gern setzen (**Excel**), entfernen
            out[int(m.group(1))] = re.sub(r"(\*\*|__)(.+?)\1", r"\2", m.group(2)).strip()
    return out


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def fidelity(original: str, cleaned: str) -> tuple[bool, str]:
    """Prüft, ob die bereinigte Zeile noch dieselbe Aussage ist. → (ok, grund)"""
    if not cleaned.strip():
        return (not _words(original)), "leer"
    a, b = _words(original), _words(cleaned)
    if not a:
        return False, "Original leer"
    word_ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # Buchstabenebene: Zusammen-/Getrenntschreibung („Ein Beinstand“ → „Einbeinstand“) ist keine Inhaltsänderung
    ca, cb = "".join(a), "".join(b)
    char_ratio = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()
    ratio = max(word_ratio, char_ratio)
    new_nums = set(re.findall(r"\d+", cleaned)) - set(re.findall(r"\d+", original))
    # Neue Ziffern sind nur erlaubt, wenn im Original Zahlwörter standen (drei → 3, zweitausend → 2000)
    exact = {"null", "eins", "ein", "eine", "einen", "einem", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht",
             "neun", "elf", "zwölf", "halb", "halbe", "viertel", "erste", "ersten", "zweite", "zweiten", "dritte", "dritten"}
    stems = ("zehn", "zig", "zwanzig", "dreißig", "hundert", "tausend", "million", "milliarde")
    spoken_numbers = any(w in exact or any(st in w for st in stems) for w in a)
    if len(cb) > len(ca) * 1.35 + 12:
        return False, "deutlich länger als das Original"
    if len(cb) < len(ca) * 0.5 - 8:
        return False, "deutlich kürzer als das Original"
    if ratio < 0.45:
        return False, f"zu stark verändert ({ratio:.0%} Übereinstimmung)"
    if new_nums and not spoken_numbers:
        return False, f"neue Zahlen: {', '.join(sorted(new_nums))}"
    return True, ""


def apply_cleaned(segments: list[Segment], cleaned: dict[int, str]) -> tuple[list[dict], int, int]:
    """Bereinigte Zeilen prüfen. → (Änderungen [{idx, clean, ok, grund}], übernommen, verworfen)"""
    changes, ok_n, bad_n = [], 0, 0
    for s in segments:
        if s.idx not in cleaned:
            continue
        c = cleaned[s.idx]
        ok, why = fidelity(s.text, c)
        if ok:
            ok_n += 1
            changes.append({"idx": s.idx, "clean": c, "ok": True, "grund": ""})
        else:
            bad_n += 1
            changes.append({"idx": s.idx, "clean": "", "ok": False, "grund": why})
            log.info("Bereinigung S%s verworfen: %s", s.idx, why)
    return changes, ok_n, bad_n


def chunks(segments: list[Segment], max_chars: int = 3500) -> list[list[Segment]]:
    out, cur, n = [], [], 0
    for s in segments:
        if cur and n + len(s.text) > max_chars:
            out.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += len(s.text) + 10
    if cur:
        out.append(cur)
    return out


def clean_segments(llm, segments: list[Segment], glossar: list[dict], context: list[Segment] | None = None) -> dict[int, str]:
    """Bereinigt eine Liste von Segmenten per KI (in Blöcken). → {idx: bereinigter Text}"""
    result: dict[int, str] = {}
    prev = list(context or [])
    for block in chunks(segments):
        ctx = ""
        if prev:
            ctx = "Vorheriger Zusammenhang (nicht ausgeben, nur zum Verständnis):\n" + \
                  "\n".join(s.text for s in prev[-3:]) + "\n\n"
        user = f"{RULES}\n\n{glossar_hint(glossar)}\n{ctx}ZEILEN:\n{build_lines(block)}"
        answer = llm.chat(SYSTEM, user, temperature=0.0)
        result.update(parse_lines(answer))
        prev = block
    return result
