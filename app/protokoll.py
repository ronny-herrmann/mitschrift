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


def verify(t: Transcript, protokoll_md: str) -> dict:
    """Prüft jede Inhaltszeile auf Belege. Liefert Statistik + markierte Zeilen."""
    valid = {s.idx for s in t.segments}
    lines_out = []
    unbelegt = 0
    ungueltig = 0
    inhalt = 0
    for raw in protokoll_md.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        is_content = bool(stripped) and not is_heading and stripped.lower() not in ("keine", "- keine", "keine.")
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
        lines_out.append({"text": line, "refs": refs, "status": status, "heading": is_heading})
    return {
        "zeilen_inhalt": inhalt,
        "unbelegt": unbelegt,
        "ungueltige_belege": ungueltig,
        "quote_belegt": round((inhalt - unbelegt - ungueltig) / inhalt, 3) if inhalt else 1.0,
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


EXTRACT_SYSTEM = (
    "Du bist ein sorgfältiger Schriftführer einer deutschen Stadtverwaltung. Du extrahierst aus einem "
    "Transkript nur das, was tatsächlich gesagt wurde, und belegst jeden Punkt mit einem wörtlichen Zitat."
)

EXTRACT_USER = """Extrahiere aus dem Transkript alle protokollrelevanten Bausteine.

Gib ausschließlich JSON zurück in genau dieser Form:
{{"themen": ["Thema 1", "Thema 2"],
 "bausteine": [
   {{"id": "B1", "typ": "aussage|beschluss|aufgabe|frage|information", "thema": "…",
     "text": "neutral formulierter Inhalt (ein Satz)", "sprecher": "…oder leer",
     "segment": 12, "zitat": "wörtlich aus Segment 12 kopiert", "unklar": false}}
 ]}}

Regeln:
- "segment" ist die Segmentnummer S… aus dem Transkript, "zitat" ein wörtlicher Ausschnitt genau dieses Segments (keine Umformulierung).
- Nichts ergänzen, nichts deuten. Unsichere Stellen: "unklar": true.
- Beschluss nur, wenn ausdrücklich beschlossen/abgestimmt/vereinbart wurde. Aufgabe nur, wenn jemand etwas übernimmt oder beauftragt wird.
- Small Talk, Begrüßungen, Wiederholungen weglassen.

TRANSKRIPT (Segmentnummer | Zeit | Sprecher):
{transkript}"""

WRITE_SYSTEM = (
    "Du bist Schriftführer einer deutschen Stadtverwaltung und schreibst Protokolle ausschließlich aus "
    "vorgeprüften Bausteinen. Du fügst keine Inhalte hinzu."
)

WRITE_USER = """Schreibe ein {stilname} für die Sitzung „{titel}" ausschließlich aus den folgenden Bausteinen.

{stilregeln}

{basisregeln}

Zusätzlich: Verwende für den Beleg die Segmentnummer des Bausteins ("segment"), also z. B. [S12]. Bausteine mit "unklar": true übernimmst du mit dem Zusatz [unklar]. Bausteine mit "beleg_ok": false lässt du weg.

BAUSTEINE (JSON):
{bausteine}"""


def _blocks(lines: list[str], max_chars: int) -> list[list[str]]:
    out, cur, n = [], [], 0
    for line in lines:
        if cur and n + len(line) > max_chars:
            out.append(cur)
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append(cur)
    return out


def create_protokoll(t: Transcript, style: str, llm: LLMClient, max_chars: int = 9000) -> dict:
    """Zweistufige, belegte Protokollerstellung über eine OpenAI-kompatible API."""
    style = style if style in STYLES else "zusammenfassung"
    transkript = transcript_for_prompt(t)
    seg_by_idx = {s.idx: s for s in t.segments}

    # Stufe 1: Bausteine extrahieren – blockweise, damit auch kleine Modelle mit begrenztem
    # Kontext lange Sitzungen verarbeiten können
    bausteine: list[dict] = []
    themen: list[str] = []
    for block in _blocks(transkript.splitlines(), max_chars):
        raw = llm.chat(EXTRACT_SYSTEM, EXTRACT_USER.format(transkript="\n".join(block)), temperature=0.0, json_mode=True)
        try:
            data = _parse_json(raw)
        except Exception:
            log.warning("Block-Extraktion lieferte kein JSON – Block übersprungen")
            continue
        bausteine.extend(data.get("bausteine") or [])
        themen.extend(t_ for t_ in (data.get("themen") or []) if t_ not in themen)
    data = {"themen": themen}
    for i, b in enumerate(bausteine, 1):
        b["id"] = f"B{i}"
        try:
            seg_idx = int(b.get("segment"))
        except (TypeError, ValueError):
            seg_idx = -1
        seg = seg_by_idx.get(seg_idx)
        zitat = str(b.get("zitat") or "")
        score = max(check_quote(zitat, seg.text), check_quote(zitat, seg.clean)) if seg else 0.0
        b["segment"] = seg_idx
        b["zitat_score"] = score
        b["beleg_ok"] = bool(seg) and score >= 0.6
    ok = [b for b in bausteine if b["beleg_ok"]]

    # Stufe 2: Protokoll nur aus geprüften Bausteinen
    md = llm.chat(
        WRITE_SYSTEM,
        WRITE_USER.format(
            stilname=STYLES[style], titel=t.title, stilregeln=STYLE_RULES[style], basisregeln=BASE_RULES,
            bausteine=json.dumps(ok, ensure_ascii=False, indent=1),
        ),
        temperature=0.0,
    )
    md = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", md.strip(), flags=re.S)

    # Stufe 3: Prüfung ohne KI
    pruefung = verify(t, md)
    return {
        "style": style,
        "themen": data.get("themen") or [],
        "bausteine": bausteine,
        "bausteine_verworfen": len(bausteine) - len(ok),
        "protokoll_md": md,
        "pruefung": pruefung,
        "llm": {"model": llm.model, "base_url": llm.base_url},
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
