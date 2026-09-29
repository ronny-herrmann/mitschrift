"""Protokoll-Erstellung mit Belegpflicht.

Grundprinzip gegen „die KI erfindet etwas dazu": Jede Aussage im Protokoll muss auf
ein Transkript-Segment verweisen, z. B. „Der Ausschuss vertagt TOP 3. [S12]". Der
Prüf-Durchlauf (reines Python, keine KI) markiert jede Zeile ohne gültigen Beleg.
Im Web-UI springt ein Klick auf [S12] zur Stelle im Transkript und im Audio.

Zwei Wege:
1. NOVA / Zwischenablage: `build_prompt()` erzeugt den vollständigen Prompt inkl.
   Transkript. Antwort der KI wird über `verify()` geprüft und angezeigt.
2. API (OpenAI-kompatibel: Ollama, vLLM, NOVA/BotBucket falls vorhanden): zweistufig –
   erst Bausteine mit Zitaten extrahieren und gegen das Transkript prüfen, dann das
   Protokoll ausschließlich aus den Bausteinen schreiben.
"""

from __future__ import annotations

import difflib
import json
import logging
import re

import httpx

from .export import fmt_time
from .store import Transcript

log = logging.getLogger(__name__)

STYLES = {
    "zusammenfassung": "Zusammenfassung",
    "ergebnis": "Ergebnisprotokoll",
    "verlauf": "Verlaufsprotokoll",
}

STYLE_RULES = {
    "zusammenfassung": (
        "Form: strukturierte Zusammenfassung für Leser mit wenig Zeit. Gliederung: 1) Worum ging es (2–3 Sätze), "
        "2) Kernaussagen als Stichpunkte, 3) Entscheidungen, 4) Aufgaben (wer, was, bis wann – nur wenn genannt), "
        "5) Offene Fragen. Knapp, keine Wiederholungen."
    ),
    "ergebnis": (
        "Form: Ergebnisprotokoll. Gliederung: 1) Teilnehmende/Sprecher (nur falls erkennbar), "
        "2) Tagesordnungspunkte bzw. Themen in der Reihenfolge des Gesprächs, je Thema die "
        "wesentlichen Ergebnisse in knappen Sätzen, 3) Beschlüsse, 4) Aufgaben (wer, was, bis wann – "
        "nur wenn genannt), 5) Offene Punkte. Keine Diskussionsverläufe, keine Wertungen."
    ),
    "verlauf": (
        "Form: Verlaufsprotokoll. Chronologisch je Thema: wer hat was vorgebracht (sinngemäß, gekürzt), "
        "welche Einwände gab es, wie wurde entschieden. Am Ende eigene Abschnitte Beschlüsse und Aufgaben."
    ),
}

BASE_RULES = """Regeln (verbindlich):
- Verwende ausschließlich Inhalte aus dem Transkript. Ergänze nichts, auch kein Allgemeinwissen, keine Annahmen, keine Namen, die nicht vorkommen.
- Jede Aussage, jeder Beschluss und jede Aufgabe endet mit dem Beleg in eckigen Klammern, z. B. [S12] oder [S12, S14]. Das S steht für die Segmentnummer im Transkript.
- Unklare oder akustisch unverständliche Stellen kennzeichnest du mit [unklar] statt sie zu deuten.
- Zahlen, Daten, Beträge und Namen exakt wie im Transkript übernehmen.
- Sachlich, im Präsens, Verwaltungsdeutsch, keine Füllwörter.
- Wenn im Transkript kein Beschluss bzw. keine Aufgabe vorkommt, schreibe unter der Überschrift „keine".
- Ausgabe als Markdown mit Überschriften (##) und Aufzählungen (-)."""


def transcript_for_prompt(t: Transcript) -> str:
    lines = []
    for s in t.segments:
        sp = f" | {s.speaker}" if s.speaker else ""
        lines.append(f"[S{s.idx} | {fmt_time(s.start)}{sp}] {s.clean or s.text}")
    return "\n".join(lines)


def build_prompt(t: Transcript, style: str = "ergebnis") -> str:
    """Vollständiger Prompt für NOVA (oder jede andere KI) per Zwischenablage."""
    style = style if style in STYLES else "zusammenfassung"
    return (
        f"Erstelle aus dem folgenden Transkript ein {STYLES[style]}.\n\n"
        f"{STYLE_RULES[style]}\n\n{BASE_RULES}\n\n"
        f"Titel: {t.title}\nDauer: {fmt_time(t.duration)}\n\n"
        f"TRANSKRIPT (Segmentnummer | Zeit | Sprecher):\n{transcript_for_prompt(t)}\n"
    )


# ---------------------------------------------------------------------------
# Prüfung (ohne KI)
# ---------------------------------------------------------------------------
_REF = re.compile(r"\[\s*S\s*(\d+)(?:\s*[,;–-]\s*S?\s*(\d+))*\s*\]")
_REF_NUMS = re.compile(r"S?\s*(\d+)")


def _refs_in(line: str) -> list[int]:
    nums: list[int] = []
    for m in _REF.finditer(line):
        nums += [int(n) for n in _REF_NUMS.findall(m.group(0).strip("[] "))]
    return nums


NEUTRAL = {"keine", "keine.", "kein thema eindeutig erkennbar.", "dazu wurde nichts belegbares gesagt.",
           "keine belegbaren inhalte in diesem abschnitt."}


def verify(t: Transcript, protokoll_md: str) -> dict:
    """Prüft jede Inhaltszeile auf Belege. Liefert Statistik + markierte Zeilen."""
    valid = {s.idx for s in t.segments}
    lines_out = []
    unbelegt = 0
    ungueltig = 0
    schwach = 0
    inhalt = 0
    for raw in protokoll_md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        is_content = bool(stripped) and not is_heading and stripped.lower().lstrip("- ") not in NEUTRAL
        refs = _refs_in(line) if is_content else []
        bad = [r for r in refs if r not in valid]
        status = "ok" if is_content else "neutral"
        if is_content:
            inhalt += 1
            if "[unklar]" in stripped.lower() and not refs:
                status = "unklar"
            elif not refs:
                status = "unbelegt"
                unbelegt += 1
            elif bad:
                status = "ungueltig"
                ungueltig += 1
            else:
                near = {r + d for r in refs for d in (-1, 0, 1)}
                src = " ".join((s.clean or s.text) for s in t.segments if s.idx in near)
                if support(stripped, src) < 0.34:
                    status = "schwach"
                    schwach += 1
        lines_out.append({"text": line, "refs": refs, "status": status, "heading": is_heading})
    return {
        "zeilen_inhalt": inhalt,
        "unbelegt": unbelegt,
        "ungueltige_belege": ungueltig,
        "schwach_belegt": schwach,
        "quote_belegt": round((inhalt - unbelegt - ungueltig - schwach) / inhalt, 3) if inhalt else 1.0,
        "zeilen": lines_out,
    }


def check_quote(quote: str, segment_text: str) -> float:
    """Wie gut passt ein angebliches Zitat zum Segmenttext? (0..1, unscharf)."""
    q = " ".join(quote.lower().split())
    s = " ".join(segment_text.lower().split())
    if not q or not s:
        return 0.0
    if q in s:
        return 1.0
    # bestes Fenster gleicher Länge im Segment suchen
    best = 0.0
    n = len(q)
    step = max(1, n // 4)
    for i in range(0, max(1, len(s) - n + 1), step):
        r = difflib.SequenceMatcher(None, q, s[i:i + n]).ratio()
        if r > best:
            best = r
    return round(best, 3)


# ---------------------------------------------------------------------------
# KI-Anbindung (OpenAI-kompatibel)
# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(self, base_url: str, api_key: str = "", model: str = "", timeout_s: int = 300):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def chat(self, system: str, user: str, temperature: float = 0.0, json_mode: bool = False) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict = {
            "model": self.model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            if r.status_code == 400 and json_mode:  # Server kennt response_format nicht
                body.pop("response_format")
                r = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ---------------------------------------------------------------------------
# Inhaltsprüfung: Steht das, was die KI behauptet, wirklich in den belegten Segmenten?
# ---------------------------------------------------------------------------
_STOP = set("""aber alle allem allen aller alles also andere anderen auch auf aus bei beim bereits bitte damit dann darauf
darf darum dass dazu dein denn dem den der des dessen die dies diese diesem diesen dieser dieses doch dort durch eine einem
einen einer eines einige einmal etwa etwas euch euer für gegen gibt habe haben hat hatte hier hinter ihm ihn ihr ihre ihrem
ihren ihrer immer indem jede jedem jeden jeder jetzt kann kein keine keinen können könnte machen macht mehr mein mich mir mit
muss müssen nach nicht noch nun nur oder ohne schon sehr sein seine seinem seinen seiner selbst sich sie sind sollen sollte
sondern sowie über um und uns unser unter viel vom von vor wann war waren warum was weil weiter welche wenn werden wird wie
wieder will wir wird wo wurde wurden zum zur zwar zwischen sagt sagte sagen gesagt erklärt betont weist hin geht ging worden
thema sitzung besprechung gespräch transkript abschnitt teilnehmende sprecher sprecherin person personen""".split())


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[a-zA-ZäöüÄÖÜß0-9]{4,}", text.lower())
    return [w for w in words if w not in _STOP]


def support(statement: str, source: str) -> float:
    """Anteil der inhaltstragenden Wörter der Aussage, die (als Wortstamm) in der Quelle vorkommen. 0..1"""
    words = _content_words(re.sub(r"\[[^\]]*\]", " ", statement))
    if not words:
        return 1.0
    src = " ".join(_content_words(source))
    hit = sum(1 for w in words if w[:5] in src)
    return hit / len(words)


# ---------------------------------------------------------------------------
# KI-gestützte Protokollerstellung (für kleine lokale Modelle ausgelegt)
#   1. Die KI notiert je Abschnitt Aussagen in einem festen Zeilenformat mit Segmentnummer.
#   2. Python prüft jede Zeile gegen die belegten Segmente und verwirft alles, was dort nicht steht.
#   3. Python baut daraus die gewählte Protokollart – die Struktur erfindet nichts dazu.
# ---------------------------------------------------------------------------
EXTRACT_SYSTEM = (
    "Du bist ein sorgfältiger Schriftführer. Du notierst nur, was im Transkript tatsächlich gesagt wurde, "
    "und nennst zu jeder Zeile die Segmentnummer, aus der sie stammt. Du erfindest nichts."
)

EXTRACT_USER = """Lies den folgenden Abschnitt eines Transkripts und notiere die Inhalte.

Schreibe jede Zeile genau in einer dieser Formen:
THEMA: worum es in diesem Abschnitt geht, ein kurzer Satz [S<Nummer>]
AUSSAGE: ein wichtiger Inhalt, ein Satz mit den Worten des Transkripts [S<Nummer>]
BESCHLUSS: nur wenn ausdrücklich etwas beschlossen oder vereinbart wurde [S<Nummer>]
AUFGABE: nur wenn jemand etwas übernimmt – Wer | Was | Bis wann (Unbekanntes als –) [S<Nummer>]
FRAGE: nur wenn eine Frage offen geblieben ist [S<Nummer>]

Regeln:
- Genau eine THEMA-Zeile, höchstens {max_aussagen} AUSSAGE-Zeilen.
- Nur Inhalte aus diesem Abschnitt, keine Annahmen, kein Allgemeinwissen, keine Namen, die nicht vorkommen.
- Die Nummer in eckigen Klammern ist die Segmentnummer, aus der der Inhalt stammt, z. B. [S12].
- Keine Überschriften, keine Einleitung, keine Erklärungen.
{top_regel}
ABSCHNITT (Segmentnummer | Zeit | Sprecher):
{transkript}"""

_ITEM = re.compile(r"^\s*[-*•]?\s*\**(THEMA|AUSSAGE|BESCHLUSS|AUFGABE|FRAGE)\**\s*(?:[(\[]\s*TOP\s*(\d+)\s*[)\]])?\s*:\s*(.+?)\s*$",
                   re.IGNORECASE)

TOP_REGEL = """- Die Sitzung hat diese Tagesordnung:
{liste}
  Schreibe bei AUSSAGE, BESCHLUSS, AUFGABE und FRAGE direkt nach dem Wort die Nummer des passenden Punkts,
  z. B. „AUSSAGE (TOP 2): …“. Passt kein Punkt: „(TOP 0)“.
"""


def _dash(x: str) -> str:
    x = x.strip().strip("-–").strip()
    return "" if x.lower() in ("", "–", "-", "unbekannt", "offen", "keine angabe", "k. a.") else x


def parse_items(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if not m:
            continue
        typ, top, body = m.group(1).upper(), m.group(2), m.group(3)
        refs = _refs_in(body)
        body = re.sub(r"\s*\[[^\]]*\]\s*", " ", body).strip().rstrip(" -–")
        body = re.sub(r"(\*\*|__)(.+?)\1", r"\2", body)
        if not body:
            continue
        item = {"typ": typ, "text": body, "refs": refs}
        if top is not None:
            item["top"] = int(top)
        if typ == "AUFGABE" and "|" in body:
            teile = [p.strip() for p in body.split("|")] + ["", ""]
            item["wer"], item["was"], item["bis"] = _dash(teile[0]), _dash(teile[1]), _dash(teile[2])
            if not item["was"]:
                continue
            item["text"] = (f"{item['wer']}: " if item["wer"] else "") + item["was"] + (f" – bis {item['bis'].removeprefix('bis ').strip()}" if item["bis"] else "")
        out.append(item)
    return out


def _seg_blocks(t: Transcript, max_chars: int) -> list[list]:
    out, cur, n = [], [], 0
    for s in t.segments:
        line_len = len(s.clean or s.text) + 30
        if cur and n + line_len > max_chars:
            out.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += line_len
    if cur:
        out.append(cur)
    return out


def _block_text(block) -> str:
    return "\n".join(f"[S{s.idx} | {fmt_time(s.start)}{' | ' + s.speaker if s.speaker else ''}] {s.clean or s.text}"
                     for s in block)


def check_items(items: list[dict], block, min_support: float = 0.5) -> tuple[list[dict], list[dict]]:
    """Jede Zeile gegen ihre Belegstelle (± ein Nachbarsegment) prüfen. → (gültig, verworfen)"""
    by_idx = {s.idx: s for s in block}
    order = [s.idx for s in block]
    ok, bad = [], []
    whole = " ".join((s.clean or s.text) for s in block)
    for it in items:
        if it["typ"] == "THEMA":
            sc = support(it["text"], whole)
            it["support"] = round(sc, 2)
            if sc >= 0.4:
                it["refs"] = [r for r in it["refs"] if r in by_idx] or [order[0]]
                ok.append(it)
            else:
                it["grund"] = "Thema kommt im Abschnitt nicht vor"
                bad.append(it)
            continue
        refs = [r for r in it["refs"] if r in by_idx]
        best_sc, best_refs = 0.0, refs
        if refs:
            pos = sorted({order.index(r) + d for r in refs for d in (-1, 0, 1) if 0 <= order.index(r) + d < len(order)})
            best_sc = support(it["text"], " ".join((by_idx[order[p]].clean or by_idx[order[p]].text) for p in pos))
        if best_sc < min_support:
            # Falsche oder fehlende Nummer: passendstes Segment im Abschnitt suchen
            for s in block:
                sc = support(it["text"], s.clean or s.text)
                if sc > best_sc:
                    best_sc, best_refs = sc, [s.idx]
        it["support"] = round(best_sc, 2)
        if best_sc >= min_support and best_refs:
            it["refs"] = best_refs
            ok.append(it)
        else:
            it["grund"] = "steht so nicht im Transkript"
            bad.append(it)
    return ok, bad


def _ref(it: dict) -> str:
    return "[" + ", ".join(f"S{r}" for r in sorted(set(it["refs"]))) + "]"


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = " ".join(_content_words(it["text"]))[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _liste(items: list[dict], leer: str = "keine") -> list[str]:
    return [f"- {it['text']} {_ref(it)}" for it in items] or [leer]


def assemble(t: Transcript, style: str, abschnitte: list[dict], tagesordnung: list[str] | None = None) -> str:
    """Protokoll aus geprüften Zeilen bauen – rein mechanisch, ohne KI. Mit Tagesordnung gliedert sich
    das Ergebnisprotokoll nach deren Punkten."""
    alle = [it for a in abschnitte for it in a["items"]]
    typ = lambda k: _dedupe([it for it in alle if it["typ"] == k])  # noqa: E731
    themen, aussagen = typ("THEMA"), typ("AUSSAGE")
    beschl, aufg, fragen = typ("BESCHLUSS"), typ("AUFGABE"), typ("FRAGE")
    seg = {s.idx: s for s in t.segments}
    out: list[str] = []
    if style == "zusammenfassung":
        ueberblick = " ".join(f"{it['text'].rstrip('.')}. {_ref(it)}" for it in themen[:3])
        out += ["## Überblick", ueberblick or "Kein Thema eindeutig erkennbar."]
        # je Abschnitt die ersten Aussagen, insgesamt höchstens 8 – verteilt über das ganze Gespräch
        kurz: list[dict] = []
        per = max(1, 8 // max(1, len(abschnitte)))
        for a in abschnitte:
            kurz += [it for it in a["items"] if it["typ"] == "AUSSAGE"][:per]
        out += ["", "## Kernaussagen", *_liste(_dedupe(kurz)[:8])]
        out += ["", "## Entscheidungen", *_liste(beschl), "", "## Aufgaben", *_liste(aufg),
                "", "## Offene Fragen", *_liste(fragen)]
    elif style == "ergebnis" and tagesordnung:
        for n, titel in enumerate(tagesordnung, 1):
            sub = _dedupe([it for it in alle if it.get("top") == n and it["typ"] != "THEMA"])
            out += [f"## TOP {n}: {titel}"]
            order = {"BESCHLUSS": "Beschluss: ", "AUFGABE": "Aufgabe: ", "FRAGE": "Offen: "}
            out += [f"- {order.get(it['typ'], '')}{it['text']} {_ref(it)}" for it in sub] or ["Dazu wurde nichts Belegbares gesagt."]
            out.append("")
        rest = _dedupe([it for it in alle if it["typ"] != "THEMA" and not (1 <= (it.get("top") or 0) <= len(tagesordnung))])
        if rest:
            out += ["## Sonstiges", *[f"- {it['text']} {_ref(it)}" for it in rest], ""]
        out += ["## Beschlüsse im Überblick", *_liste(beschl), "", "## Aufgaben", *_liste(aufg)]
    elif style == "ergebnis":
        out += ["## Themen", *([f"{i}. {it['text']} {_ref(it)}" for i, it in enumerate(themen, 1)] or ["keine"])]
        out += ["", "## Ergebnisse"]
        for i, a in enumerate(abschnitte, 1):
            sub = [it for it in a["items"] if it["typ"] == "AUSSAGE"]
            if not sub:
                continue
            th = next((it for it in a["items"] if it["typ"] == "THEMA"), None)
            out.append(f"### {i}. {th['text'] if th else 'Abschnitt ab ' + fmt_time(a['start'])}")
            out += _liste(sub)
        if not aussagen:
            out.append("keine")
        out += ["", "## Beschlüsse", *_liste(beschl), "", "## Aufgaben", *_liste(aufg),
                "", "## Offene Punkte", *_liste(fragen)]
    else:  # verlauf
        for a in abschnitte:
            th = next((it for it in a["items"] if it["typ"] == "THEMA"), None)
            out.append(f"## {fmt_time(a['start'])} – {fmt_time(a['end'])}" + (f": {th['text']}" if th else ""))
            rows = [it for it in a["items"] if it["typ"] in ("AUSSAGE", "BESCHLUSS", "AUFGABE", "FRAGE")]
            for it in rows:
                sp = seg[it["refs"][0]].speaker if it["refs"] and it["refs"][0] in seg else ""
                label = {"BESCHLUSS": "Beschluss: ", "AUFGABE": "Aufgabe: ", "FRAGE": "Offene Frage: "}.get(it["typ"], "")
                out.append(f"- {('**' + sp + ':** ') if sp else ''}{label}{it['text']} {_ref(it)}")
            if not rows:
                out.append("- Keine belegbaren Inhalte in diesem Abschnitt.")
            out.append("")
        out += ["## Beschlüsse", *_liste(beschl), "", "## Aufgaben", *_liste(aufg)]
    return "\n".join(out).strip() + "\n"


def transcript_hash(t: Transcript, tagesordnung: list[str] | None = None) -> str:
    import hashlib
    h = hashlib.sha1("|".join(tagesordnung or []).encode())
    for s in t.segments:
        h.update(f"{s.idx}|{s.speaker}|{s.clean or s.text}\n".encode())
    return h.hexdigest()[:16]


def extract(t: Transcript, llm: LLMClient, max_chars: int = 5000, progress=None, workers: int = 2,
            tagesordnung: list[str] | None = None) -> dict:
    """Stufe 1+2: Zeilen je Abschnitt von der KI holen (bis zu `workers` gleichzeitig) und prüfen."""
    from .bereinigung import parallel_blocks
    blocks = _seg_blocks(t, max_chars)

    def run(i, block):
        max_aussagen = max(3, min(8, len(block) // 3))
        top_regel = TOP_REGEL.format(liste="\n".join(f"  TOP {n}: {x}" for n, x in enumerate(tagesordnung, 1))) \
            if tagesordnung else ""
        raw = llm.chat(EXTRACT_SYSTEM, EXTRACT_USER.format(transkript=_block_text(block), max_aussagen=max_aussagen,
                                                           top_regel=top_regel), temperature=0.0)
        return check_items(parse_items(raw), block)

    results = parallel_blocks(blocks, run, lambda b: sum(len(s.clean or s.text) for s in b), workers, progress)
    abschnitte, verworfen = [], []
    for block, (ok, bad) in zip(blocks, results):
        abschnitte.append({"start": block[0].start, "end": block[-1].end, "items": ok})
        verworfen += bad
    return {"hash": transcript_hash(t, tagesordnung), "abschnitte": abschnitte, "verworfen": verworfen}


def create_protokoll(t: Transcript, style: str, llm: LLMClient | None, max_chars: int = 5000,
                     progress=None, cache: dict | None = None, workers: int = 2,
                     tagesordnung: list[str] | None = None) -> dict:
    """Belegte Protokollerstellung. Mit gültigem Zwischenspeicher (cache) ohne erneuten KI-Aufruf."""
    style = style if style in STYLES else "zusammenfassung"
    if cache and cache.get("hash") == transcript_hash(t, tagesordnung):
        ex = cache
    else:
        if llm is None:
            raise ValueError("Keine KI angebunden")
        ex = extract(t, llm, max_chars, progress, workers, tagesordnung)
    n_ok = sum(len(a["items"]) for a in ex["abschnitte"])
    if not n_ok:
        raise ValueError("Im Transkript wurden keine belegbaren Inhalte gefunden. Es wird kein Protokoll erzeugt, "
                         "damit nichts erfunden wird.")
    md = assemble(t, style, ex["abschnitte"], tagesordnung)
    return {
        "style": style,
        "protokoll_md": md,
        "pruefung": verify(t, md),
        "verworfen": len(ex["verworfen"]),
        "verworfen_beispiele": [v["text"] for v in ex["verworfen"][:5]],
        "extrakt": ex,
        "llm": {"model": getattr(llm, "model", "") if llm else "Zwischenspeicher"},
    }


# ---------------------------------------------------------------------------
# Bereinigtes Wortprotokoll ohne KI (nur eindeutige Verzögerungslaute entfernen –
# Wörter wie „also", „genau", „halt" bleiben stehen, weil sie Bedeutung tragen können)
# ---------------------------------------------------------------------------
_FILLERS = re.compile(r"(?<!\w)(ähm+|äh+|ähh+|hm+|mhm+|öhm+)(?!\w)[,.]?\s*", re.IGNORECASE)


def bereinigt(text: str) -> str:
    out = _FILLERS.sub("", text)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    # Satzanfänge groß (auch nach entferntem Verzögerungslaut)
    out = re.sub(r"(^|[.!?]\s+)([a-zäöü])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out
