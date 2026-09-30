"""Warteschlange: Uploads transkribieren und Live-Aufnahmen verfeinern (ein Hintergrund-Thread).

Nacheinander statt parallel – so bleibt die CPU für die Live-Transkription frei und der
Server kann nicht durch viele gleichzeitige Uploads überlastet werden.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from . import bereinigung
from .audio import decode_to_pcm16k
from .config import Settings
from .pipeline import Transcriber
from .store import Store

log = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, transcriber: Transcriber, store: Store, settings: Settings, llm_factory=None, glossar_for=None):
        self.transcriber = transcriber
        self.store = store
        self.settings = settings
        self.llm_factory = llm_factory
        self.glossar_for = glossar_for or (lambda tid: transcriber.glossar_entries())
        self._diarizer = None
        self._diarizer_failed = False
        self._q: queue.Queue[str] = queue.Queue()
        self._progress: dict[str, tuple[int, int]] = {}
        self._started: dict[str, tuple[float, float]] = {}   # tid → (Startzeit, geschätzte Dauer in s)
        self._thread = threading.Thread(target=self._run, name="jobs", daemon=True)
        self._thread.start()

    def enqueue(self, transcript_id: str) -> None:
        self._q.put(transcript_id)

    def progress(self, transcript_id: str) -> tuple[int, int] | None:
        return self._progress.get(transcript_id)

    TEMPO_START = 0.12   # Sekunden Rechenzeit je Sekunde Audio (genaue Erkennung + Sprecher), wird gelernt

    def info(self, transcript_id: str) -> dict | None:
        """Fortschritt für die Anzeige: Schritt, vergangene Zeit, geschätzte Gesamtdauer."""
        p = self._progress.get(transcript_id)
        st = self._started.get(transcript_id)
        if not p and not st:
            return None
        d = {"done": p[0], "total": p[1]} if p else {"done": 0, "total": 0}
        if st:
            d["elapsed"] = round(time.time() - st[0], 1)
            d["eta"] = round(st[1], 1)
        return d

    @property
    def pending(self) -> int:
        return self._q.qsize()

    def resume_unfinished(self) -> None:
        """Nach einem Neustart: angefangene Jobs erneut einreihen."""
        for t in self.store.list_transcripts(limit=10_000):
            if t.status in ("processing", "refining") and t.audio_path and Path(t.audio_path).exists():
                log.info("Setze unterbrochenen Job fort: %s", t.id)
                self.enqueue(t.id)
            elif t.status in ("processing",) and not (t.audio_path and Path(t.audio_path).exists()):
                self.store.update_transcript(t.id, status="error", error="Audio fehlt nach Neustart")
            elif t.status == "refining":
                self.store.update_transcript(t.id, status="done")

    def _run(self) -> None:
        while True:
            tid = self._q.get()
            try:
                self._process(tid)
            except Exception as e:
                log.exception("Job %s fehlgeschlagen", tid)
                t = self.store.get_transcript(tid)
                if t and t.status == "refining" and t.segments:
                    # Verfeinerung fehlgeschlagen → Live-Ergebnis bleibt gültig
                    self.store.update_transcript(tid, status="done", error=f"Verfeinerung fehlgeschlagen: {e}"[:500])
                else:
                    self.store.update_transcript(tid, status="error", error=str(e)[:500])
            finally:
                self._progress.pop(tid, None)
                self._started.pop(tid, None)
                self._q.task_done()

    def _process(self, tid: str) -> None:
        t = self.store.get_transcript(tid)
        if not t or not t.audio_path or t.status not in ("processing", "refining"):
            return
        refine = t.status == "refining"
        log.info("%s %s (%s)", "Verfeinere" if refine else "Transkribiere", tid, t.title)
        t0 = time.time()
        audio = decode_to_pcm16k(t.audio_path)
        duration = len(audio) / 16_000
        self.store.update_transcript(tid, duration=round(duration, 2))
        tempo = float(self.store.get_setting("tempo:verarbeitung", self.TEMPO_START))
        self._started[tid] = (t0, max(4.0, duration * tempo + 2.0))

        def prog(done: int, total: int) -> None:
            self._progress[tid] = (done, total)

        entries = self.glossar_for(tid)
        segments, secs = self.transcriber.transcribe_audio(audio, prog, entries=entries)

        # Sprechererkennung
        if self.settings.diarization and segments:
            self._progress[tid] = (-1, 0)  # Anzeige: „Sprecher werden erkannt“
            diar = self._get_diarizer()
            if diar is not None:
                try:
                    from .diarize import assign_speakers
                    segments = assign_speakers(segments, diar.turns(audio))
                except Exception:
                    log.exception("Sprechererkennung fehlgeschlagen – weiter ohne")

        # Manuell vergebene Namen aus der Live-Fassung übernehmen (nach Zeit-Überlappung)
        if refine:
            old = self.store.get_transcript(tid)
            named = [s for s in (old.segments if old else []) if s.speaker and not s.speaker.startswith("Sprecher ")]
            for seg in segments:
                best, overlap = "", 0.0
                for o in named:
                    ov = min(seg.end, o.end) - max(seg.start, o.start)
                    if ov > overlap:
                        best, overlap = o.speaker, ov
                if best:
                    seg.speaker = best

        # KI-Bereinigung (falls eine KI angebunden ist)
        llm = self.llm_factory() if (self.llm_factory and self.settings.auto_ai_clean) else None
        if llm is not None and segments:
            self._progress[tid] = (-2, 0)  # Anzeige: „KI bereinigt“
            try:
                cleaned = bereinigung.clean_segments(llm, segments, entries,
                                                     korrekturen=self.store.get_setting(f"korrekturen:{tid}", []))
                changes, _, _ = bereinigung.apply_cleaned(segments, cleaned)
                by_idx = {c["idx"]: c for c in changes}
                for seg in segments:
                    c = by_idx.get(seg.idx)
                    if c:
                        seg.clean, seg.clean_note = (c["clean"], "") if c["ok"] else ("", c["grund"])
            except Exception:
                log.exception("KI-Bereinigung fehlgeschlagen – Transkript bleibt unbereinigt")

        self.store.replace_segments(tid, segments)
        model = self.transcriber.final_backend.info().get("model", "")
        fields = dict(status="done", processing_seconds=round(secs, 2), error="")
        if refine:
            fields["model"] = f"{t.model} → {model}" if model and model not in t.model else t.model
        else:
            fields["model"] = model or t.model
        self.store.update_transcript(tid, **fields)
        if duration >= 20:   # Tempo lernen (gleitender Mittelwert), damit die Restzeit stimmt
            neu = (time.time() - t0) / duration
            self.store.set_setting("tempo:verarbeitung", round(0.6 * tempo + 0.4 * neu, 4))
        if self.settings.audio_delete_after_done:
            self.store.delete_audio(tid)

    def _get_diarizer(self):
        if self._diarizer is None and not self._diarizer_failed:
            try:
                from .diarize import Diarizer
                s = self.settings
                self._diarizer = Diarizer(s.models_dir, s.diarization_threshold, s.diarization_speakers)
            except Exception:
                self._diarizer_failed = True
                log.exception("Sprechererkennung nicht verfügbar")
        return self._diarizer
        log.info("Fertig: %s – %.1fs Audio in %.1fs (%.0f× Echtzeit)", tid, duration, secs, duration / max(secs, 0.01))
