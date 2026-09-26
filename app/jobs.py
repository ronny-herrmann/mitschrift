"""Warteschlange für hochgeladene Dateien – ein Hintergrund-Thread arbeitet sie nacheinander ab."""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from .audio import decode_to_pcm16k
from .pipeline import Transcriber, asr_executor
from .store import Store
from .config import Settings

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

    def _run(self) -> None:
        while True:
            tid = self._q.get()
            try:
                self._process(tid)
            except Exception as e:
                log.exception("Job %s fehlgeschlagen", tid)
                self.store.update_transcript(tid, status="error", error=str(e)[:500])
            finally:
                self._progress.pop(tid, None)
                self._q.task_done()

    def _process(self, tid: str) -> None:
        t = self.store.get_transcript(tid)
        if not t or not t.audio_path:
            return
        log.info("Transkribiere Upload %s (%s)", tid, t.title)
        audio = decode_to_pcm16k(t.audio_path)
        duration = len(audio) / 16_000
        self.store.update_transcript(tid, duration=round(duration, 2))

        def prog(done: int, total: int) -> None:
            self._progress[tid] = (done, total)

        # Über den gemeinsamen Pool laufen lassen, damit Live und Upload sich nicht in die Quere kommen
        fut = asr_executor(self.settings.asr_workers).submit(self.transcriber.transcribe_audio, audio, prog)
        segments, secs = fut.result()
        self.store.replace_segments(tid, segments)
        self.store.update_transcript(tid, status="done", processing_seconds=round(secs, 2))
        log.info("Fertig: %s – %.1fs Audio in %.1fs (RTF %.2f)", tid, duration, secs, secs / max(duration, 0.01))
