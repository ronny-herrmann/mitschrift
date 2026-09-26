"""NVIDIA Parakeet TDT 0.6B v3 (25 europäische Sprachen inkl. Deutsch) über onnx-asr.

- Lizenz Modell: CC-BY-4.0 (NVIDIA), Laufzeit onnx-asr: MIT, onnxruntime: MIT
- int8-quantisiert läuft es auf einer Büro-CPU deutlich schneller als Echtzeit.
- Erster Start lädt die Modelldateien von Hugging Face in MODELS_DIR; danach offline.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .base import ASRBackend, Utterance, Word


class ParakeetBackend(ASRBackend):
    name = "parakeet"

    def __init__(
        self,
        model: str = "nemo-parakeet-tdt-0.6b-v3",
        quantization: str | None = "int8",
        models_dir: str | Path = "./models",
        device: str = "cpu",
        threads: int | None = None,
    ):
        import onnx_asr
        import onnxruntime as ort

        self.model_name = model
        self.quantization = quantization or None
        self.device = device
        local_dir = Path(models_dir) / model.replace("/", "__")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads or max(1, (os.cpu_count() or 2))
        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        have_files = local_dir.exists() and any(local_dir.glob("*.onnx")) and (local_dir / "config.json").exists()
        if have_files:
            # Offline: nur lokale Dateien, kein Netzwerkzugriff
            adapter = onnx_asr.load_model(
                model, path=str(local_dir), quantization=self.quantization, sess_options=opts, providers=providers
            )
        else:
            # Erster Start: Modelldateien nach local_dir laden (braucht einmalig Zugriff auf huggingface.co)
            from onnx_asr.loader import Manager

            local_dir.parent.mkdir(parents=True, exist_ok=True)
            manager = Manager(opts, providers, None, None, None)
            adapter = manager.create_asr(model, str(local_dir), quantization=self.quantization, offline=False)
        self._model = adapter.with_timestamps()

    def _to_utterance(self, res) -> Utterance:
        text = (res.text or "").strip()
        words: list[Word] = []
        if res.tokens and res.timestamps:
            cur: list[str] = []
            start = 0.0
            last_end = 0.0
            for tok, ts in zip(res.tokens, res.timestamps):
                if tok.startswith("▁") and cur:
                    words.append(Word("".join(cur).replace("▁", ""), start, last_end))
                    cur = []
                if not cur:
                    start = float(ts)
                cur.append(tok)
                last_end = float(ts) + 0.08
            if cur:
                w = "".join(cur).replace("▁", "").strip()
                if w:
                    words.append(Word(w, start, last_end))
            words = [w for w in words if w.text]
        return Utterance(text=text, words=words)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Utterance:
        res = self._model.recognize(audio.astype(np.float32), sample_rate=16_000)
        return self._to_utterance(res)

    def transcribe_batch(self, audios: list[np.ndarray], language: str | None = None) -> list[Utterance]:
        if not audios:
            return []
        results = self._model.recognize([a.astype(np.float32) for a in audios], sample_rate=16_000)
        return [self._to_utterance(r) for r in results]

    def info(self) -> dict:
        return {
            "backend": self.name,
            "model": self.model_name,
            "quantization": self.quantization,
            "device": self.device,
            "license": "CC-BY-4.0 (Modell), MIT (Laufzeit)",
        }
