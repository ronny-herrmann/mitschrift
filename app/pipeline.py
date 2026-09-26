"""Transkriptions-Pipeline: Audio → VAD-Segmente → Spracherkennung → Segmente mit Zeitstempeln.

Zwei-Modell-Strategie:
- live_backend: schnell (Parakeet), für Text während der Aufnahme
- final_backend: genau (z. B. Whisper-German), für Uploads und die Verfeinerung nach dem Stopp
  → wird im Hintergrund geladen; schlägt das Laden fehl, wird das Live-Modell verwendet.
"""

from __future__ import annotations

import logging
import threading
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

_executor: ThreadPoolExecutor | None = None


def asr_executor(workers: int = 1) -> ThreadPoolExecutor:
    """Gemeinsamer Pool für Live-Modellaufrufe (onnxruntime/CTranslate2 geben den GIL frei)."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="asr")
    return _executor


class Transcriber:
    def __init__(
        self,
        backend: ASRBackend,
        settings: Settings,
        glossar: Callable[[], list[dict]] | None = None,
        final_factory: Callable[[], ASRBackend] | None = None,
    ):
        self.backend = backend
        self.settings = settings
        self.vad = SileroVAD()
        self._vad_lock = threading.Lock()
        self._glossar = glossar or (lambda: [])
        self._final: ASRBackend | None = None
        self.final_status = "gleich wie live"
        if final_factory is not None:
            self.final_status = "lädt …"
            threading.Thread(target=self._load_final, args=(final_factory,), daemon=True, name="load-final").start()

    # --- genaues Modell im Hintergrund laden -------------------------------------
    def _load_final(self, factory: Callable[[], ASRBackend]) -> None:
        t0 = time.perf_counter()
        try:
            b = factory()
            b.warmup()
            self._final = b
            self.final_status = f"bereit ({time.perf_counter() - t0:.0f}s)"
            log.info("Genaues Modell bereit: %s", b.info())
        except Exception as e:
            self.final_status = f"Fehler: {e} – nutze Live-Modell"
            log.exception("Genaues Modell konnte nicht geladen werden – nutze Live-Modell")

    @property
    def final_backend(self) -> ASRBackend:
        return self._final or self.backend

    def info(self) -> dict:
        return {"live": self.backend.info(), "final": self.final_backend.info(), "final_status": self.final_status}

    # --- Hilfsfunktionen ---------------------------------------------------
    def vad_kwargs(self, offline: bool = False) -> dict:
        s = self.settings
        return dict(
            threshold=s.vad_threshold,
            min_silence_ms=s.offline_min_silence_ms if offline else s.vad_min_silence_ms,
            max_segment_s=s.offline_max_segment_s if offline else s.vad_max_segment_s,
            min_speech_ms=s.vad_min_speech_ms,
            pad_ms=s.vad_pad_ms,
        )

    def glossar_entries(self) -> list[dict]:
        return self._glossar()

    def utterance_to_segment(self, idx: int, speech: SpeechSegment, utt: Utterance, rules=None) -> Segment:
        rules = rules if rules is not None else compile_glossar(self._glossar())
        text = apply_glossar(utt.text.strip(), rules)
        words = [
            {"w": w.text, "s": round(speech.start_s + w.start, 2), "e": round(speech.start_s + w.end, 2)}
            for w in utt.words
        ]
        return Segment(idx=idx, start=round(speech.start_s, 2), end=round(speech.end_s, 2), text=text, words=words)

    # --- Transkription (offline, genaues Modell) ---------------------------
    def transcribe_audio(
        self,
        audio: np.ndarray,
        progress: Callable[[int, int], None] | None = None,
        batch_size: int = 4,
        backend: ASRBackend | None = None,
    ) -> tuple[list[Segment], float]:
        """Ganze Aufnahme transkribieren → (Segmente, Rechenzeit in Sekunden)."""
        backend = backend or self.final_backend
        t0 = time.perf_counter()
        with self._vad_lock:
            speech = segment_audio(audio, self.vad, **self.vad_kwargs(offline=True))
        entries = self._glossar()
        rules = compile_glossar(entries)
        if hasattr(backend, "set_hotwords"):
            backend.set_hotwords([e["zu"] for e in entries])
        segments: list[Segment] = []
        lang = self.settings.language
        if progress:
            progress(0, len(speech))
        for i in range(0, len(speech), batch_size):
            batch = speech[i:i + batch_size]
            utts = backend.transcribe_batch([s.audio for s in batch], language=lang)
            for sp, utt in zip(batch, utts):
                if utt.text.strip():
                    segments.append(self.utterance_to_segment(len(segments), sp, utt, rules))
            if progress:
                progress(min(i + batch_size, len(speech)), len(speech))
        return segments, time.perf_counter() - t0

    def transcribe_file(self, path: str, progress=None) -> tuple[list[Segment], float, float]:
        audio = decode_to_pcm16k(path)
        segments, secs = self.transcribe_audio(audio, progress)
        return segments, len(audio) / SAMPLE_RATE, secs
