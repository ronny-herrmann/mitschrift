"""Warteschlange: Uploads transkribieren und Live-Aufnahmen verfeinern (ein Hintergrund-Thread).

Nacheinander statt parallel – so bleibt die CPU für die Live-Transkription frei und der
Server kann nicht durch viele gleichzeitige Uploads überlastet werden.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from .audio import decode_to_pcm16k
from .config import Settings
from .pipeline import Transcriber
from .store import Store

log = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, transcriber: Transcriber, store: Store, settings: Settings):
        self.transcriber = transcriber
        self.store = store
        self.settings = settings
        self._q: queue.Queue[str] = queue.Queue()
        self._progress: dict[str, tuple[int, int]] = {}
        self._thread = threading.Thread(target=self._run, name="jobs", daemon=True)
        self._thread.start()

    def enqueue(self, transcript_id: str) -> None:
        self._q.put(transcript_id)

    def progress(self, transcript_id: str) -> tuple[int, int] | None:
        return self._progress.get(transcript_id)

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
                self._q.task_done()

    def _process(self, tid: str) -> None:
        t = self.store.get_transcript(tid)
        if not t or not t.audio_path or t.status not in ("processing", "refining"):
            return
        refine = t.status == "refining"
        log.info("%s %s (%s)", "Verfeinere" if refine else "Transkribiere", tid, t.title)
        audio = decode_to_pcm16k(t.audio_path)
        duration = len(audio) / 16_000
        self.store.update_transcript(tid, duration=round(duration, 2))

        def prog(done: int, total: int) -> None:
            self._progress[tid] = (done, total)

        segments, secs = self.transcriber.transcribe_audio(audio, prog)

        # Manuell vergebene Sprechernamen aus der Live-Fassung übernehmen (nach Zeit-Überlappung)
        if refine:
            old = self.store.get_transcript(tid)
            named = [s for s in (old.segments if old else []) if s.speaker]
            for seg in segments:
                best, overlap = "", 0.0
                for o in named:
                    ov = min(seg.end, o.end) - max(seg.start, o.start)
                    if ov > overlap:
                        best, overlap = o.speaker, ov
                if best:
                    seg.speaker = best

        self.store.replace_segments(tid, segments)
        model = self.transcriber.final_backend.info().get("model", "")
        fields = dict(status="done", processing_seconds=round(secs, 2), error="")
        if refine:
            fields["model"] = f"{t.model} → {model}" if model and model not in t.model else t.model
        else:
            fields["model"] = model or t.model
        self.store.update_transcript(tid, **fields)
        if self.settings.audio_delete_after_done:
            self.store.delete_audio(tid)
        log.info("Fertig: %s – %.1fs Audio in %.1fs (%.0f× Echtzeit)", tid, duration, secs, duration / max(secs, 0.01))
