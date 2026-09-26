#!/usr/bin/env python3
"""Modelle einmalig herunterladen (auf einem Rechner mit Internet), danach läuft alles offline.

    python scripts/download_models.py                 # Standard: Parakeet + Whisper-German
    python scripts/download_models.py --only parakeet

Die Dateien landen in MODELS_DIR (Standard ./models). Den Ordner kann man auf den
Zielserver kopieren (USB-Stick, Fileshare) – dort ist dann kein Internetzugang nötig.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.asr import create_backend  # noqa: E402
from app.config import Settings  # noqa: E402


def folder_size(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["parakeet", "whisper"], help="nur ein Modell laden")
    args = ap.parse_args()
    s = Settings()
    names = [args.only] if args.only else ["parakeet", "whisper"]
    for name in names:
        t0 = time.perf_counter()
        print(f"Lade {name} …")
        try:
            b = create_backend(s, name)
            b.warmup()
            print(f"  OK in {time.perf_counter() - t0:.0f}s: {b.info()}")
        except Exception as e:
            print(f"  FEHLER: {e}")
    print(f"\nModelle-Ordner: {s.models_dir.resolve()}  ({folder_size(s.models_dir):.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
