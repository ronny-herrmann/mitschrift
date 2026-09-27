"""NVIDIA Parakeet TDT 0.6B v3 (25 europäische Sprachen inkl. Deutsch) über sherpa-onnx.

- Modell: CC-BY-4.0 (NVIDIA); Laufzeit sherpa-onnx: Apache-2.0; onnxruntime: MIT
- int8-quantisiert (~670 MB). Gemessen auf 2 CPU-Kernen: 12 s Audio in 1,3 s (≈9× Echtzeit).
- Erster Start lädt das Modellpaket von GitHub (k2-fsa/sherpa-onnx Releases) nach MODELS_DIR;
  danach läuft alles offline. Alternativ Ordner manuell kopieren.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from .. import modelcache
from .base import ASRBackend, Utterance, Word

log = logging.getLogger(__name__)

MODEL_DIRNAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{MODEL_DIRNAME}.tar.bz2"
REQUIRED = ["encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"]


def ensure_model(models_dir: str | Path, url: str = MODEL_URL) -> Path:
    """Stellt sicher, dass das Modell lokal liegt; lädt es sonst einmalig herunter.
    Mehrere Instanzen mit gemeinsamem Modell-Ordner warten aufeinander (Dateisperre)."""
    models_dir = Path(models_dir)
    target = models_dir / MODEL_DIRNAME
    if all((target / f).exists() for f in REQUIRED):
        return target
    with modelcache.download_lock(models_dir):
        if all((target / f).exists() for f in REQUIRED):  # die andere Instanz war schneller
            return target
        log.info("Lade Parakeet-Modell (~490 MB) …")
        modelcache.fetch_archive(url, models_dir, MODEL_DIRNAME)
    missing = [f for f in REQUIRED if not (target / f).exists()]
    if missing:
        raise RuntimeError(f"Modellpaket unvollständig, fehlt: {missing}")
    return target


class ParakeetBackend(ASRBackend):
    name = "parakeet"

    def __init__(self, models_dir: str | Path = "./models", device: str = "cpu", threads: int | None = None,
                 model_dir: str | Path | None = None):
        import sherpa_onnx

        d = Path(model_dir) if model_dir else ensure_model(models_dir)
        self.model_dir = d
        self.device = device
        self.threads = threads or max(1, os.cpu_count() or 2)

        def load():
            return sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(d / "encoder.int8.onnx"),
                decoder=str(d / "decoder.int8.onnx"),
                joiner=str(d / "joiner.int8.onnx"),
                tokens=str(d / "tokens.txt"),
                num_threads=self.threads,
                model_type="nemo_transducer",
                decoding_method="greedy_search",
                provider="cuda" if device == "cuda" else "cpu",
            )

        try:
            self._rec = load()
        except Exception:
            if model_dir:  # manuell vorgegebener Ordner: nicht anfassen
                raise
            # Beschädigter Download (z. B. abgebrochen) → einmal neu laden
            modelcache.purge(d)
            d = ensure_model(models_dir)
            self._rec = load()
        # sherpa-onnx-Recognizer ist threadsicher für getrennte Streams, wir serialisieren trotzdem
        # pro Aufruf, damit Live und Upload sich nicht gegenseitig die Kerne halbieren.
        self._lock = threading.Lock()

    @staticmethod
    def _to_utterance(res) -> Utterance:
        text = (res.text or "").strip()
        words: list[Word] = []
        cur, start, last = "", 0.0, 0.0
        for tok, ts in zip(res.tokens or [], res.timestamps or []):
            ts = float(ts)
            if tok.startswith(" ") and cur:
                words.append(Word(cur.strip(), start, last))
                cur = ""
            if not cur:
                start = ts
            cur += tok
            last = ts + 0.08
        if cur.strip():
            words.append(Word(cur.strip(), start, last))
        return Utterance(text=text, words=words)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Utterance:
        return self.transcribe_batch([audio], language)[0]

    def transcribe_batch(self, audios: list[np.ndarray], language: str | None = None) -> list[Utterance]:
        if not audios:
            return []
        with self._lock:
            streams = []
            for a in audios:
                s = self._rec.create_stream()
                s.accept_waveform(16_000, a.astype(np.float32))
                streams.append(s)
            self._rec.decode_streams(streams)
            return [self._to_utterance(s.result) for s in streams]

    def info(self) -> dict:
        return {
            "backend": self.name,
            "model": "parakeet-tdt-0.6b-v3",
            "quantization": "int8",
            "device": self.device,
            "threads": self.threads,
            "license": "CC-BY-4.0 (Modell), Apache-2.0 (sherpa-onnx)",
        }
