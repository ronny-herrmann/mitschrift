"""faster-whisper (CTranslate2) – z. B. deutsch-feingetuntes Whisper Large v3 Turbo.

Standard: primeline/whisper-large-v3-turbo-german, vorkonvertiert und int8-quantisiert
(Hugging Face: cstr/whisper-large-v3-turbo-german-int8_float32). Andere CTranslate2-
Modelle (z. B. Systran/faster-whisper-large-v3-turbo) lassen sich per WHISPER_MODEL setzen.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .base import ASRBackend, Utterance, Word


class WhisperBackend(ASRBackend):
    name = "whisper"

    def __init__(
        self,
        model: str = "cstr/whisper-large-v3-turbo-german-int8_float32",
        compute_type: str = "int8",
        models_dir: str | Path = "./models",
        device: str = "cpu",
        beam_size: int = 1,
        threads: int | None = None,
    ):
        from faster_whisper import WhisperModel

        self.model_name = model
        self.compute_type = compute_type
        self.device = device
        self.beam_size = beam_size
        self._hotwords: str | None = None
        self._model = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            download_root=str(models_dir),
            cpu_threads=threads or max(1, (os.cpu_count() or 2)),
        )

    def set_hotwords(self, words: list[str]) -> None:
        """Glossar-Begriffe als Hinweis an das Modell (verbessert Eigennamen und Fachwörter)."""
        words = [w for w in dict.fromkeys(w.strip() for w in words) if w]
        self._hotwords = ", ".join(words[:60]) or None

    def transcribe(self, audio: np.ndarray, language: str | None = "de") -> Utterance:
        segments, _info = self._model.transcribe(
            audio.astype(np.float32),
            language=language or None,
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=False,                  # Segmentierung übernimmt unsere VAD
            condition_on_previous_text=False,  # verhindert Wiederholungs-Halluzinationen
            temperature=[0.0, 0.2, 0.4],       # bei unsicherem Ergebnis neu versuchen
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.2,   # verwirft Endlosschleifen-Text
            hotwords=self._hotwords,
        )
        texts: list[str] = []
        words: list[Word] = []
        for seg in segments:
            texts.append(seg.text.strip())
            for w in seg.words or []:
                words.append(Word(w.word.strip(), float(w.start), float(w.end)))
        return Utterance(text=" ".join(t for t in texts if t), words=words)

    def info(self) -> dict:
        return {
            "backend": self.name,
            "model": self.model_name,
            "compute_type": self.compute_type,
            "device": self.device,
            "beam_size": self.beam_size,
            "license": "MIT/Apache-2.0 (faster-whisper, Whisper); Modellkarte beachten",
        }
