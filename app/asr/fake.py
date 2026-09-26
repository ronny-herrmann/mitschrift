"""Deterministisches Fake-Backend für Tests und UI-Entwicklung ohne Modell.

Gibt pro Segment einen Platzhaltertext mit Dauer zurück, optional mit vorgegebenen
Texten (für Tests: `FakeBackend(texts=[...])`).
"""

from __future__ import annotations

import numpy as np

from .base import ASRBackend, Utterance, Word


class FakeBackend(ASRBackend):
    name = "fake"

    def __init__(self, texts: list[str] | None = None):
        self._texts = list(texts or [])
        self._i = 0

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Utterance:
        dur = len(audio) / 16_000
        if self._texts:
            text = self._texts[self._i % len(self._texts)]
            self._i += 1
        else:
            text = f"[Testtext für {dur:.1f} Sekunden Audio]"
        words = []
        toks = text.split()
        if toks:
            step = dur / len(toks)
            words = [Word(t, i * step, (i + 1) * step) for i, t in enumerate(toks)]
        return Utterance(text=text, words=words)

    def info(self) -> dict:
        return {"backend": self.name, "model": "fake", "note": "Kein echtes Modell – nur für Tests"}
