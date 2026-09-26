"""Sprachaktivitätserkennung (VAD) mit Silero VAD (ONNX, MIT-Lizenz) – streaming-fähig.

Die Modell-Datei liegt im Repo (app/models/silero_vad.onnx), es ist also kein
Download nötig. Silero v5 erwartet Blöcke von 512 Samples (32 ms bei 16 kHz) mit
64 Samples Kontext aus dem vorherigen Block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import onnxruntime as ort

from .audio import SAMPLE_RATE

CHUNK = 512  # Samples pro VAD-Block
CONTEXT = 64
CHUNK_MS = CHUNK * 1000 / SAMPLE_RATE  # 32 ms

_DEFAULT_MODEL = Path(__file__).parent / "models" / "silero_vad.onnx"


class SileroVAD:
    """Dünner Wrapper um das Silero-ONNX-Modell mit internem Zustand (streaming)."""

    def __init__(self, model_path: str | Path | None = None):
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path or _DEFAULT_MODEL), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def prob(self, chunk: np.ndarray) -> float:
        """Sprachwahrscheinlichkeit für genau einen 512-Sample-Block (float32)."""
        if chunk.shape[0] != CHUNK:
            chunk = np.pad(chunk, (0, CHUNK - chunk.shape[0]))[:CHUNK]
        x = np.concatenate([self._context, chunk[None, :].astype(np.float32)], axis=1)
        out, state = self.session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = state
        self._context = x[:, -CONTEXT:]
        return float(out[0][0])

    def probs(self, audio: np.ndarray) -> np.ndarray:
        """Wahrscheinlichkeiten für eine ganze Aufnahme (setzt den Zustand zurück)."""
        self.reset()
        n = int(np.ceil(len(audio) / CHUNK))
        padded = np.pad(audio.astype(np.float32), (0, n * CHUNK - len(audio)))
        return np.array([self.prob(padded[i * CHUNK:(i + 1) * CHUNK]) for i in range(n)], dtype=np.float32)


@dataclass
class SpeechSegment:
    start: int  # Sample-Index (inkl. Vorlauf)
    end: int    # Sample-Index (exkl.)
    audio: np.ndarray

    @property
    def start_s(self) -> float:
        return self.start / SAMPLE_RATE

    @property
    def end_s(self) -> float:
        return self.end / SAMPLE_RATE


@dataclass
class Segmenter:
    """Zustandsautomat, der aus VAD-Wahrscheinlichkeiten abgeschlossene Sprachsegmente bildet.

    Wird sowohl live (Block für Block) als auch offline (alle Blöcke nacheinander) benutzt,
    damit Live- und Datei-Transkription identisch segmentieren.
    """

    threshold: float = 0.5
    min_silence_ms: int = 700
    max_segment_s: float = 15.0
    min_speech_ms: int = 250
    pad_ms: int = 300

    _pos: int = 0                    # Sample-Position des nächsten Blocks
    _speaking: bool = False
    _speech_start: int = 0
    _silence_ms: float = 0.0
    _speech_ms: float = 0.0
    _buf: list[np.ndarray] = field(default_factory=list)   # Blöcke seit Segmentstart (inkl. Vorlauf)
    _buf_start: int = 0
    _pre: list[np.ndarray] = field(default_factory=list)   # Ringpuffer für Vorlauf

    @property
    def neg_threshold(self) -> float:
        return max(self.threshold - 0.15, 0.01)

    @property
    def _pre_chunks(self) -> int:
        return max(1, int(round(self.pad_ms / CHUNK_MS)))

    def push(self, chunk: np.ndarray, prob: float) -> list[SpeechSegment]:
        """Einen 512-Sample-Block mit seiner Sprachwahrscheinlichkeit verarbeiten."""
        out: list[SpeechSegment] = []
        pos = self._pos
        self._pos += len(chunk)

        if not self._speaking:
            self._pre.append(chunk)
            if len(self._pre) > self._pre_chunks:
                self._pre.pop(0)
            if prob >= self.threshold:
                self._speaking = True
                self._speech_start = pos
                self._silence_ms = 0.0
                self._speech_ms = CHUNK_MS
                self._buf = list(self._pre)
                self._buf_start = pos - CHUNK * (len(self._pre) - 1)
                self._pre = []
            return out

        self._buf.append(chunk)
        if prob >= self.threshold:
            self._silence_ms = 0.0
            self._speech_ms += CHUNK_MS
        elif prob < self.neg_threshold:
            self._silence_ms += CHUNK_MS
        else:  # Hysterese-Zone: weder eindeutig Sprache noch Stille
            self._silence_ms += CHUNK_MS / 2

        total_s = (self._pos - self._buf_start) / SAMPLE_RATE
        if self._silence_ms >= self.min_silence_ms:
            seg = self._emit(trim_silence=True)
            if seg:
                out.append(seg)
        elif total_s >= self.max_segment_s:
            seg = self._emit(trim_silence=False)
            if seg:
                out.append(seg)
        return out

    def flush(self) -> list[SpeechSegment]:
        """Am Ende der Aufnahme: laufendes Segment abschließen."""
        if not self._speaking:
            return []
        seg = self._emit(trim_silence=False)
        return [seg] if seg else []

    def _emit(self, *, trim_silence: bool) -> SpeechSegment | None:
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        start = self._buf_start
        end = start + len(audio)
        if trim_silence:
            # Nachlauf auf pad_ms begrenzen (Rest der Stille abschneiden)
            keep = int((self.min_silence_ms - self.pad_ms) / CHUNK_MS)
            if keep > 0:
                audio = audio[: max(CHUNK, len(audio) - keep * CHUNK)]
                end = start + len(audio)
        speech_ms = self._speech_ms
        self._speaking = False
        # Die letzten (stillen) Blöcke als Vorlauf für das nächste Segment behalten
        self._pre = list(self._buf[-self._pre_chunks:])
        self._buf = []
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        if speech_ms < self.min_speech_ms or len(audio) < CHUNK:
            return None
        return SpeechSegment(start=max(start, 0), end=end, audio=audio)


def segment_audio(audio: np.ndarray, vad: SileroVAD, **kwargs) -> list[SpeechSegment]:
    """Offline: ganze Aufnahme in Sprachsegmente zerlegen (gleiche Logik wie live)."""
    seg = Segmenter(**kwargs)
    probs = vad.probs(audio)
    n = len(probs)
    padded = np.pad(audio.astype(np.float32), (0, n * CHUNK - len(audio)))
    result: list[SpeechSegment] = []
    for i in range(n):
        result.extend(seg.push(padded[i * CHUNK:(i + 1) * CHUNK], float(probs[i])))
    result.extend(seg.flush())
    # Segmente dürfen nicht über das Ende hinausgehen
    for s in result:
        if s.end > len(audio):
            s.audio = s.audio[: max(0, len(audio) - s.start)]
            s.end = len(audio)
    return [s for s in result if len(s.audio) >= CHUNK]
