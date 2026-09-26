"""Transkriptions-Pipeline: Audio → VAD-Segmente → Spracherkennung → Segmente mit Zeitstempeln.

Wird für Datei-Uploads und für den zweiten Durchlauf nach einer Live-Aufnahme genutzt.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import numpy as np

from .asr.base import ASRBackend, Utterance
from .audio import SAMPLE_RATE, decode_to_pcm16k
from .config import Settings
from .glossar import apply_glossar, compile_glossar
from .store import Segment
from .vad import SileroVAD, SpeechSegment, segment_audio

log = logging.getLogger(__name__)

# Ein gemeinsamer Thread-Pool für alle Modellaufrufe (Live + Uploads), damit die CPU
# nicht überbucht wird. onnxruntime/CTranslate2 geben den GIL frei.
_executor: ThreadPoolExecutor | None = None


def asr_executor(workers: int = 1) -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="asr")
    return _executor


class Transcriber:
    def __init__(self, backend: ASRBackend, settings: Settings, glossar: Callable[[], list[dict]] | None = None):
        self.backend = backend
        self.settings = settings
        self.vad = SileroVAD()
        self._glossar = glossar or (lambda: [])

    # --- Hilfsfunktionen ---------------------------------------------------
    def vad_kwargs(self) -> dict:
        s = self.settings
        return dict(
            threshold=s.vad_threshold,
            min_silence_ms=s.vad_min_silence_ms,
            max_segment_s=s.vad_max_segment_s,
            min_speech_ms=s.vad_min_speech_ms,
            pad_ms=s.vad_pad_ms,
        )

    def utterance_to_segment(self, idx: int, speech: SpeechSegment, utt: Utterance, rules=None) -> Segment:
        rules = rules if rules is not None else compile_glossar(self._glossar())
        text = apply_glossar(utt.text.strip(), rules)
        words = [
            {"w": w.text, "s": round(speech.start_s + w.start, 2), "e": round(speech.start_s + w.end, 2)}
            for w in utt.words
        ]
        return Segment(idx=idx, start=round(speech.start_s, 2), end=round(speech.end_s, 2), text=text, words=words)

    # --- Transkription -----------------------------------------------------
    def transcribe_audio(
        self, audio: np.ndarray, progress: Callable[[int, int], None] | None = None, batch_size: int = 8
    ) -> tuple[list[Segment], float]:
        """Ganze Aufnahme transkribieren. Liefert Segmente und benötigte Rechenzeit in Sekunden."""
        t0 = time.perf_counter()
        speech = segment_audio(audio, self.vad, **self.vad_kwargs())
        rules = compile_glossar(self._glossar())
        segments: list[Segment] = []
        lang = self.settings.language
        for i in range(0, len(speech), batch_size):
            batch = speech[i:i + batch_size]
            utts = self.backend.transcribe_batch([s.audio for s in batch], language=lang)
            for j, (sp, utt) in enumerate(zip(batch, utts)):
                if not utt.text.strip():
                    continue
                segments.append(self.utterance_to_segment(len(segments), sp, utt, rules))
            if progress:
                progress(min(i + batch_size, len(speech)), len(speech))
        return segments, time.perf_counter() - t0

    def transcribe_file(self, path: str, progress: Callable[[int, int], None] | None = None) -> tuple[list[Segment], float, float]:
        """Datei transkribieren → (Segmente, Audiodauer, Rechenzeit)."""
        audio = decode_to_pcm16k(path)
        segments, secs = self.transcribe_audio(audio, progress)
        return segments, len(audio) / SAMPLE_RATE, secs
