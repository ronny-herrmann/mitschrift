"""Gemeinsame Schnittstelle aller Spracherkennungs-Backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Word:
    text: str
    start: float  # Sekunden, relativ zum übergebenen Audio
    end: float


@dataclass
class Utterance:
    """Ergebnis für ein Stück Audio (ein Sprachsegment)."""

    text: str
    words: list[Word] = field(default_factory=list)


class ASRBackend(ABC):
    """Ein Backend transkribiert 16-kHz-mono-float32-Audio.

    Die Segmentierung (VAD) passiert außerhalb – Backends bekommen in der Regel
    Stücke von wenigen Sekunden und müssen daher keine Langform-Logik haben.
    """

    name: str = "base"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Utterance:
        ...

    def transcribe_batch(self, audios: list[np.ndarray], language: str | None = None) -> list[Utterance]:
        return [self.transcribe(a, language) for a in audios]

    def info(self) -> dict:
        return {"backend": self.name}

    def warmup(self) -> None:
        """Einmal ein kurzes Stück durchrechnen, damit die erste echte Anfrage schnell ist."""
        self.transcribe(np.zeros(16_000, dtype=np.float32))
