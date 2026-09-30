"""Konfiguration über Umgebungsvariablen (.env wird beim Start eingelesen).

Alle Pfade sind relativ zum Arbeitsverzeichnis, in Docker also /data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Parser (keine Abhängigkeit von python-dotenv nötig)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(Path(os.environ.get("MITSCHRIFT_ENV_FILE", ".env")))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


@dataclass
class Settings:
    # --- Allgemein -------------------------------------------------------
    app_name: str = "Protokollant"
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", "./models")))
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "info"))

    # --- Spracherkennung -------------------------------------------------
    # parakeet | whisper | fake
    asr_backend: str = field(default_factory=lambda: _env("ASR_BACKEND", "parakeet"))
    # Sprache (ISO-Code). Whisper/Canary nutzen sie, Parakeet erkennt selbst.
    language: str = field(default_factory=lambda: _env("LANGUAGE", "de"))
    # cpu | cuda
    device: str = field(default_factory=lambda: _env("DEVICE", "cpu"))
    # Anzahl paralleler Transkriptions-Threads (CPU: 1–2, GPU: 2–4)
    asr_workers: int = field(default_factory=lambda: _env_int("ASR_WORKERS", 1))

    # Modell für den genauen Durchlauf (Uploads + Verfeinerung nach Live-Aufnahme).
    # Leer = gleiches Modell wie live. Empfehlung für den Test: whisper (deutsch-feingetunt).
    final_asr_backend: str = field(default_factory=lambda: _env("FINAL_ASR_BACKEND", ""))

    # Parakeet (sherpa-onnx): optional fester Ordner mit dem Modell (sonst MODELS_DIR, Auto-Download)
    parakeet_model_dir: str = field(default_factory=lambda: _env("PARAKEET_MODEL_DIR", ""))

    # faster-whisper: CTranslate2-Modell (Hugging-Face-ID oder lokaler Pfad)
    whisper_model: str = field(
        default_factory=lambda: _env("WHISPER_MODEL", "cstr/whisper-large-v3-turbo-german-int8_float32")
    )
    whisper_compute_type: str = field(default_factory=lambda: _env("WHISPER_COMPUTE_TYPE", "int8"))
    whisper_beam_size: int = field(default_factory=lambda: _env_int("WHISPER_BEAM_SIZE", 1))

    # --- Sprachaktivitätserkennung (VAD) für die Live-Segmentierung --------
    vad_threshold: float = field(default_factory=lambda: _env_float("VAD_THRESHOLD", 0.5))
    # Pause in ms, nach der ein Satz als abgeschlossen gilt und transkribiert wird
    vad_min_silence_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SILENCE_MS", 550))
    # Segment wird spätestens nach so vielen Sekunden geschnitten
    vad_max_segment_s: float = field(default_factory=lambda: _env_float("VAD_MAX_SEGMENT_S", 12.0))
    # Grauer Zwischentext während des Sprechens (alle n Sekunden aktualisiert)
    live_partials: bool = field(default_factory=lambda: _env_bool("LIVE_PARTIALS", True))
    live_partial_interval_s: float = field(default_factory=lambda: _env_float("LIVE_PARTIAL_INTERVAL_S", 1.0))
    # KI-Bereinigung jedes fertigen Satzes schon während der Aufnahme (nur mit LLM_*)
    live_ai_clean: bool = field(default_factory=lambda: _env_bool("LIVE_AI_CLEAN", True))
    # Automatisch bereinigen (live und nach dem Stopp)? Standard: nein – die Nutzenden klicken selbst auf „Bereinigen“.
    auto_ai_clean: bool = field(default_factory=lambda: _env_bool("AUTO_AI_CLEAN", False))
    vad_min_speech_ms: int = field(default_factory=lambda: _env_int("VAD_MIN_SPEECH_MS", 250))
    vad_pad_ms: int = field(default_factory=lambda: _env_int("VAD_PAD_MS", 300))

    # Offline (Upload/Verfeinerung): längere Segmente = mehr Kontext = bessere Erkennung
    offline_max_segment_s: float = field(default_factory=lambda: _env_float("OFFLINE_MAX_SEGMENT_S", 28.0))
    offline_min_silence_ms: int = field(default_factory=lambda: _env_int("OFFLINE_MIN_SILENCE_MS", 1200))
    # Sehr kurze Sprachstücke (Sekunden) mit dem Nachbarn zusammenlegen – mehr Zusammenhang für die Erkennung (0 = aus)
    offline_merge_short_s: float = field(default_factory=lambda: _env_float("OFFLINE_MERGE_SHORT_S", 2.5))

    # Nach Ende einer Live-Aufnahme: Verfeinerung über die ganze Datei (läuft im Hintergrund)
    live_final_pass: bool = field(default_factory=lambda: _env_bool("LIVE_FINAL_PASS", True))

    # Sprechererkennung im genauen Durchlauf (pyannote-Segmentierung + 3D-Speaker, sherpa-onnx)
    diarization: bool = field(default_factory=lambda: _env_bool("DIARIZATION", True))
    diarization_threshold: float = field(default_factory=lambda: _env_float("DIARIZATION_THRESHOLD", 0.85))
    # Feste Sprecherzahl, falls bekannt (0 = automatisch)
    diarization_speakers: int = field(default_factory=lambda: _env_int("DIARIZATION_SPEAKERS", 0))
    # Rechenkerne für die Sprechererkennung (läuft in eigenem Prozess parallel zur Spracherkennung)
    diarization_threads: int = field(default_factory=lambda: _env_int("DIARIZATION_THREADS", 3))

    # --- Protokoll-KI (optional, OpenAI-kompatibel: Ollama, vLLM, NOVA/BotBucket …) ----
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_timeout_s: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT_S", 300))
    # Wie viele Abschnitte gleichzeitig an das Sprachmodell gehen (passend zu --parallel des llama.cpp-Servers)
    llm_parallel: int = field(default_factory=lambda: _env_int("LLM_PARALLEL", 2))

    # --- Speicherung / Löschung (Datensparsamkeit) ------------------------
    # Audio direkt nach fertiger Transkription löschen (dann kein Abspielen/Nachhören möglich)
    audio_delete_after_done: bool = field(default_factory=lambda: _env_bool("AUDIO_DELETE_AFTER_DONE", False))
    # Automatische Löschfristen in Stunden (0 = keine Frist)
    retention_audio_hours: int = field(default_factory=lambda: _env_int("RETENTION_AUDIO_HOURS", 0))
    retention_transcript_hours: int = field(default_factory=lambda: _env_int("RETENTION_TRANSCRIPT_HOURS", 0))

    # --- Zugriff ----------------------------------------------------------
    # Passwort für die Anmeldung (leer = kein Login, nur für localhost!). SSO folgt in Stufe 2.
    access_password: str = field(default_factory=lambda: _env("ACCESS_PASSWORD", ""))
    # Geheimer Schlüssel für Sitzungs-Cookies (wird bei leerem Wert zufällig erzeugt → Neustart = neu anmelden)
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET", ""))
    session_hours: int = field(default_factory=lambda: _env_int("SESSION_HOURS", 12))
    # Cookie nur über HTTPS senden (hinter Caddy: 1)
    cookie_secure: bool = field(default_factory=lambda: _env_bool("COOKIE_SECURE", False))

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "mitschrift.sqlite3"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


settings = Settings()
settings.audio_dir.mkdir(parents=True, exist_ok=True)
settings.models_dir.mkdir(parents=True, exist_ok=True)
