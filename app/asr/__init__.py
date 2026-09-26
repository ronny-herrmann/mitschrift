"""Backend-Registry: wählt anhand der Konfiguration das Spracherkennungs-Backend."""

from __future__ import annotations

import logging

from ..config import Settings
from .base import ASRBackend, Utterance, Word

log = logging.getLogger(__name__)

__all__ = ["ASRBackend", "Utterance", "Word", "create_backend"]


def create_backend(s: Settings, name: str | None = None) -> ASRBackend:
    name = (name or s.asr_backend).lower()
    log.info("Lade Spracherkennung: %s", name)
    if name == "fake":
        from .fake import FakeBackend

        return FakeBackend()
    if name == "parakeet":
        from .parakeet import ParakeetBackend

        return ParakeetBackend(models_dir=s.models_dir, device=s.device, model_dir=s.parakeet_model_dir or None)
    if name == "whisper":
        from .whisper import WhisperBackend

        return WhisperBackend(
            model=s.whisper_model,
            compute_type=s.whisper_compute_type,
            models_dir=s.models_dir,
            device=s.device,
            beam_size=s.whisper_beam_size,
        )
    raise ValueError(f"Unbekanntes Backend: {name} (erlaubt: parakeet, whisper, fake)")
