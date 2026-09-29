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
- Korrigiere: falsch erkannte Wörter, Rechtschreibung, Zeichensetzung, Groß-/Kleinschreibung, Zahlen und Datumsangaben in üblicher Schreibweise.
- Englische oder sinnlose Wörter mitten in einem deutschen Satz sind fast immer Erkennungsfehler. Ersetze sie durch das deutsche Wort, das nach Klang und Zusammenhang gemeint ist.
- Entferne: Verzögerungslaute (äh, ähm, hm), direkte Wortwiederholungen und Stotterer.
- Verboten: zusammenfassen, umformulieren, Sätze umstellen, Inhalte ergänzen oder weglassen, Namen/Zahlen erfinden.
- Vorgegebene Schreibweisen (Glossar und Korrekturen der Nutzer) sind verbindlich und haben Vorrang vor deinem Sprachgefühl.
- Wenn eine Stelle unklar ist: unverändert lassen.
- Nur die Zeilen ausgeben, keine Erklärungen.

Beispiele:
[S1] ähm ja also wir haben die tabelle im CHS äh gepflegt
→ [S1] Ja, also wir haben die Tabelle im Excel gepflegt.
[S2] wir treffen uns am dritten zehnten um zehn uhr in der in der kämmerei
→ [S2] Wir treffen uns am 3.10. um 10 Uhr in der Kämmerei.
[S3] wir sehen uns dann nächste week im bürger meister amt
→ [S3] Wir sehen uns dann nächste Woche im Bürgermeisteramt.
[S4] das müssen wir bis frei tag mit dem amt klären
→ [S4] Das müssen wir bis Freitag mit dem Amt klären."""


def glossar_hint(glossar: list[dict], korrekturen: list[dict] | None = None) -> str:
    terms = [e["zu"] for e in glossar or [] if e.get("zu")]
    pairs = [f"{e['von']} → {e['zu']}" for e in glossar or [] if e.get("von") and e.get("zu")]
    user = [f"{k['von']} → {k['zu']}" for k in korrekturen or [] if k.get("von") and k.get("zu")]
    out = ""
    if terms:
        out += "Fachbegriffe und Namen, die richtig geschrieben werden müssen: " + ", ".join(dict.fromkeys(terms)) + "\n"
    if pairs:
        out += "Bekannte Fehlerkennungen: " + "; ".join(pairs) + "\n"
    if user:
        out += "Von den Nutzern korrigiert (verbindlich, genau so schreiben): " + "; ".join(user) + "\n"
    return out


def seg_text(s: Segment) -> str:
    return s.text


def build_lines(segments: list[Segment]) -> str:
    return "\n".join(f"[S{s.idx}] {seg_text(s)}" for s in segments)


def build_prompt(t: Transcript, glossar: list[dict], korrekturen: list[dict] | None = None) -> str:
    """Vollständiger Prompt für NOVA (Zwischenablage). Von Hand korrigierte Zeilen bleiben außen vor."""
    segs = [s for s in t.segments if not s.edited]
    return (f"Bereinige das folgende Transkript.\n\n{RULES}\n\n{glossar_hint(glossar, korrekturen)}\n"
            f"TRANSKRIPT:\n{build_lines(segs)}\n")


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


def apply_korrekturen(text: str, korrekturen: list[dict] | None) -> str:
    """Korrekturen der Nutzer wortgenau durchsetzen – die KI darf sie nicht zurückdrehen."""
    if not korrekturen:
        return text
    from .glossar import apply_glossar, compile_glossar
    return apply_glossar(text, compile_glossar(korrekturen))


def parallel_blocks(blocks: list, fn, weight, workers: int = 2, progress=None) -> list:
    """Blöcke gleichzeitig an die KI geben (der llama.cpp-Server rechnet mehrere Anfragen zusammen –
    auf der CPU spürbar schneller als nacheinander). Fortschritt in Zeichen, Ergebnis in Blockreihenfolge."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = sum(weight(b) for b in blocks) or 1
    done = 0
    lock = threading.Lock()
    results: list = [None] * len(blocks)
    if progress:
        progress(0, total)
    if not blocks:
        return results
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(blocks)))) as ex:
        futs = {ex.submit(fn, i, b): i for i, b in enumerate(blocks)}
        for f in as_completed(futs):
            i = futs[f]
            results[i] = f.result()
            with lock:
                done += weight(blocks[i])
                if progress:
                    progress(done, total)
    return results


def clean_segments(llm, segments: list[Segment], glossar: list[dict], context: list[Segment] | None = None,
                   korrekturen: list[dict] | None = None, progress=None, workers: int = 2) -> dict[int, str]:
    """Bereinigt eine Liste von Segmenten per KI (in Blöcken, bis zu `workers` gleichzeitig). → {idx: Text}

    Von Hand korrigierte Segmente (edited) werden nicht geschickt, dienen aber als Zusammenhang."""
    todo = [s for s in segments if not s.edited]
    blocks = chunks(todo)
    by_pos = {s.idx: n for n, s in enumerate(segments)}
    hint = glossar_hint(glossar, korrekturen)

    def run(i: int, block: list[Segment]) -> dict[int, str]:
        if i == 0:
            prev = list(context or [])
        else:
            start = by_pos.get(block[0].idx, 0)
            prev = segments[max(0, start - 3):start]
        ctx = ""
        if prev:
            ctx = "Vorheriger Zusammenhang (nicht ausgeben, nur zum Verständnis):\n" + \
                  "\n".join((s.clean or s.text) for s in prev[-3:]) + "\n\n"
        user = f"{RULES}\n\n{hint}\n{ctx}ZEILEN:\n{build_lines(block)}"
        answer = llm.chat(SYSTEM, user, temperature=0.0)
        return {idx: apply_korrekturen(txt, korrekturen) for idx, txt in parse_lines(answer).items()}

    result: dict[int, str] = {}
    for part in parallel_blocks(blocks, run, lambda b: sum(len(s.text) for s in b), workers, progress):
        result.update(part or {})
    return result


# ---------------------------------------------------------------------------
# Korrekturen der Nutzer erkennen (für „an weiteren Stellen ersetzen“ und als Vorgabe für die KI)
# ---------------------------------------------------------------------------
_TOK = re.compile(r"[\w\-]+", re.UNICODE)


def korrektur_paare(alt: str, neu: str) -> list[dict]:
    """Welche Wörter wurden ersetzt? „Bini Kum seufzte“ → „Winnie Puuh seufzte“ ⇒ [{von: Bini Kum, zu: Winnie Puuh}]"""
    a, b = _TOK.findall(alt), _TOK.findall(neu)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b]).get_opcodes():
        if op != "replace":
            continue
        von, zu = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if not (1 <= i2 - i1 <= 4 and 1 <= j2 - j1 <= 4):
            continue
        if von.lower() == zu.lower() or len(von) < 2 or von.isdigit():
            continue
        out.append({"von": von, "zu": zu})
    return out


def weitere_stellen(segments: list[Segment], paare: list[dict], ausser: int) -> list[int]:
    """Segmente, in denen eine korrigierte Schreibweise noch in der alten Form vorkommt."""
    if not paare:
        return []
    from .glossar import compile_glossar
    rules = compile_glossar(paare)
    hits = []
    for s in segments:
        if s.idx == ausser:
            continue
        cur = s.clean or s.text
        if any(p.search(cur) for p, _ in rules):
            hits.append(s.idx)
    return hits
