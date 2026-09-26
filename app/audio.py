"""Audio-Hilfsfunktionen: Dekodieren beliebiger Formate (über ffmpeg), WAV schreiben.

Intern arbeitet alles mit 16 kHz, mono, float32 im Bereich [-1, 1].
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class AudioDecodeError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def decode_to_pcm16k(path: str | Path) -> np.ndarray:
    """Dekodiert eine Audio-/Videodatei (mp3, m4a, wav, webm, mp4, ogg …) zu 16 kHz mono float32."""
    path = Path(path)
    if not path.exists():
        raise AudioDecodeError(f"Datei nicht gefunden: {path}")
    if path.suffix.lower() == ".wav":
        try:
            return _read_wav_16k(path)
        except Exception:
            pass  # kein reines PCM-16k-WAV → ffmpeg
    if not ffmpeg_available():
        raise AudioDecodeError("ffmpeg wurde nicht gefunden – bitte installieren (apt install ffmpeg).")
    cmd = [
        "ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise AudioDecodeError(proc.stderr.decode("utf-8", "ignore").strip() or "ffmpeg-Fehler")
    return pcm16_bytes_to_float(proc.stdout)


def _read_wav_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
            raise ValueError("kein 16k/mono/16bit WAV")
        frames = w.readframes(w.getnframes())
    return pcm16_bytes_to_float(frames)


def pcm16_bytes_to_float(data: bytes) -> np.ndarray:
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16_bytes(x: np.ndarray) -> bytes:
    x = np.clip(x, -1.0, 1.0)
    return (x * 32767.0).astype("<i2").tobytes()


def write_wav_16k(path: str | Path, pcm16: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm16)


def duration_seconds(samples: int) -> float:
    return samples / SAMPLE_RATE


def seconds_to_samples(sec: float) -> int:
    return int(round(sec * SAMPLE_RATE))
