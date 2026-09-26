#!/usr/bin/env python3
"""Bake-off: mehrere Spracherkennungs-Backends auf denselben Aufnahmen vergleichen.

Misst je Datei und Modell:
- Rechenzeit und RTF (Rechenzeit / Audiodauer; 0,1 = zehnmal schneller als Echtzeit)
- WER (Wortfehlerrate) und CER, wenn neben der Audiodatei eine gleichnamige .txt mit dem
  Referenz-Wortlaut liegt (z. B. sitzung3.m4a + sitzung3.txt)

Aufruf (im Projektordner, Modelle werden beim ersten Mal heruntergeladen):
    python bench/bakeoff.py --audio-dir ./feldtest --backends parakeet,whisper --out ./bench/ergebnisse

Ergebnis: CSV (alle Werte), Markdown-Tabelle (für Folien) und die Hypothesen-Texte je Modell,
damit Tester Fehler blind vergleichen können.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.asr import create_backend  # noqa: E402
from app.audio import SAMPLE_RATE, decode_to_pcm16k  # noqa: E402
from app.config import Settings  # noqa: E402
from app.pipeline import Transcriber  # noqa: E402
from bench.normalize import normalize_de  # noqa: E402

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".opus", ".webm", ".flac", ".aac", ".wma"}


def wer_cer(ref: str, hyp: str) -> tuple[float, float]:
    import jiwer

    r, h = normalize_de(ref), normalize_de(hyp)
    if not r:
        return float("nan"), float("nan")
    return jiwer.wer(r, h), jiwer.cer(r, h)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio-dir", required=True, help="Ordner mit Aufnahmen (+ optionale .txt-Referenzen)")
    ap.add_argument("--backends", default="parakeet,whisper", help="Kommagetrennt: parakeet, whisper, fake")
    ap.add_argument("--out", default="bench/ergebnisse", help="Ausgabeordner")
    ap.add_argument("--runs", type=int, default=1, help="Wiederholungen je Datei (Zeitmessung: Median)")
    ap.add_argument("--device", default=None, help="cpu oder cuda (Standard: Konfiguration)")
    args = ap.parse_args()

    audio_dir = Path(args.audio_dir)
    files = sorted(p for p in audio_dir.iterdir() if p.suffix.lower() in AUDIO_EXT)
    if not files:
        print(f"Keine Audiodateien in {audio_dir}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    if args.device:
        settings.device = args.device
    rows: list[dict] = []

    for name in [b.strip() for b in args.backends.split(",") if b.strip()]:
        print(f"\n=== Backend: {name} ===")
        t0 = time.perf_counter()
        backend = create_backend(settings, name)
        backend.warmup()
        print(f"Modell geladen in {time.perf_counter() - t0:.1f}s – {backend.info()}")
        tr = Transcriber(backend, settings)
        (out / name).mkdir(exist_ok=True)

        for f in files:
            audio = decode_to_pcm16k(f)
            dur = len(audio) / SAMPLE_RATE
            times = []
            segments = []
            for _ in range(max(1, args.runs)):
                segments, secs = tr.transcribe_audio(audio)
                times.append(secs)
            secs = sorted(times)[len(times) // 2]
            hyp = "\n".join(s.text for s in segments)
            (out / name / (f.stem + ".txt")).write_text(hyp + "\n", encoding="utf-8")
            ref_path = f.with_suffix(".txt")
            wer = cer = float("nan")
            if ref_path.exists():
                wer, cer = wer_cer(ref_path.read_text(encoding="utf-8"), hyp)
            row = {
                "datei": f.name, "backend": name, "modell": backend.info().get("model", ""), "dauer_s": round(dur, 1),
                "rechenzeit_s": round(secs, 2), "rtf": round(secs / max(dur, 0.01), 3),
                "x_echtzeit": round(dur / max(secs, 0.001), 1), "segmente": len(segments),
                "woerter": len(hyp.split()),
                "wer": round(wer, 4) if wer == wer else "", "cer": round(cer, 4) if cer == cer else "",
            }
            rows.append(row)
            print(f"  {f.name:40s} {dur:7.1f}s  Rechenzeit {secs:6.2f}s  RTF {row['rtf']:.3f}"
                  + (f"  WER {wer * 100:5.1f}%" if wer == wer else "  (keine Referenz)"))

    # CSV
    with (out / "ergebnisse.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    (out / "ergebnisse.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    # Zusammenfassung je Backend
    md = ["# Bake-off Ergebnisse", "", f"Aufnahmen: {len(files)} · Ordner: `{audio_dir}` · CPU-Kerne: {os.cpu_count()}", "",
          "| Backend | Modell | Ø RTF | Ø × Echtzeit | Ø WER | Ø CER | Dateien mit Referenz |", "|---|---|---|---|---|---|---|"]
    for name in dict.fromkeys(r["backend"] for r in rows):
        rs = [r for r in rows if r["backend"] == name]
        with_ref = [r for r in rs if r["wer"] != ""]
        avg = lambda k, src: (sum(float(r[k]) for r in src) / len(src)) if src else float("nan")  # noqa: E731
        md.append(
            f"| {name} | {rs[0]['modell']} | {avg('rtf', rs):.3f} | {avg('x_echtzeit', rs):.0f}× | "
            + (f"{avg('wer', with_ref) * 100:.1f} % | {avg('cer', with_ref) * 100:.1f} % | " if with_ref else "– | – | ")
            + f"{len(with_ref)}/{len(rs)} |"
        )
    md += ["", "## Je Datei", "", "| Datei | Backend | Dauer | Rechenzeit | RTF | WER |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['datei']} | {r['backend']} | {r['dauer_s']} s | {r['rechenzeit_s']} s | {r['rtf']} | "
                  + (f"{float(r['wer']) * 100:.1f} %" if r["wer"] != "" else "–") + " |")
    md += ["", "RTF = Rechenzeit ÷ Audiodauer (kleiner ist besser). WER = Wortfehlerrate nach deutscher Normalisierung "
           "(Groß-/Kleinschreibung, Satzzeichen, ß/ss und Verzögerungslaute werden nicht als Fehler gezählt)."]
    (out / "ergebnisse.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nErgebnisse: {out / 'ergebnisse.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
