"""Live-Transkription über WebSocket.

Drei Ebenen, wie bei guten Diktiersystemen:
1. "partial"  – grauer Zwischentext, etwa jede Sekunde neu, solange jemand spricht
2. "segment"  – fester Text, sobald eine Sprechpause erkannt wird (schwarz)
3. "clean"    – KI-bereinigte Fassung des Satzes (optional, mit Treue-Prüfung)
Nach dem Stopp: Ergebnis sofort gespeichert; Verfeinerung (genaues Modell +
Sprechererkennung) läuft als Hintergrund-Job.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

import numpy as np

from . import bereinigung
from .audio import SAMPLE_RATE, pcm16_bytes_to_float, write_wav_16k
from .config import Settings
from .pipeline import Transcriber, asr_executor
from .store import Segment, Store
from .vad import CHUNK, Segmenter, SileroVAD, SpeechSegment

log = logging.getLogger(__name__)

Sender = Callable[[dict], Awaitable[None]]


class LiveSession:
    def __init__(self, transcriber: Transcriber, store: Store, settings: Settings, send: Sender,
                 on_saved: Callable[[str, bool], None] | None = None,
                 llm_factory: Callable[[], object | None] | None = None):
        self.transcriber = transcriber
        self.store = store
        self.settings = settings
        self.send = send
        self.on_saved = on_saved
        self.llm = llm_factory() if (llm_factory and settings.live_ai_clean and settings.auto_ai_clean) else None
        self._stopped = False
        self.vad = SileroVAD()  # eigener Zustand pro Sitzung
        self.segmenter = Segmenter(**transcriber.vad_kwargs(offline=False))
        self.transcript = None
        self._pending = np.zeros(0, dtype=np.float32)
        self._raw_path: Path | None = None
        self._raw = None
        self._samples = 0
        self._queue: asyncio.Queue[SpeechSegment | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._idx = 0
        self._segments: list[Segment] = []
        self._compute = 0.0
        # Zwischenergebnisse
        self._utt = 0                 # zählt abgeschlossene Äußerungen
        self._partial_busy = False
        self._last_partial = 0.0
        self._clean_tasks: set[asyncio.Task] = set()

    # --- Lebenszyklus ---------------------------------------------------------
    async def start(self, title: str) -> str:
        self.transcript = self.store.create_transcript(
            title=title or time.strftime("Aufnahme %d.%m.%Y %H:%M"), source="live", status="processing",
            model=self.transcriber.backend.info().get("model", self.transcriber.backend.name),
            language=self.settings.language,
        )
        self._raw_path = self.settings.audio_dir / f"{self.transcript.id}.pcm"
        self._raw = open(self._raw_path, "wb")
        self._worker = asyncio.create_task(self._work())
        await self.send({"type": "ready", "transcript_id": self.transcript.id, "title": self.transcript.title,
                         "ai_clean": self.llm is not None})
        return self.transcript.id

    async def feed(self, pcm: bytes) -> None:
        if self._raw is None:
            return
        self._raw.write(pcm)
        audio = pcm16_bytes_to_float(pcm)
        self._samples += len(audio)
        buf = np.concatenate([self._pending, audio]) if len(self._pending) else audio
        n = len(buf) // CHUNK
        for i in range(n):
            chunk = buf[i * CHUNK:(i + 1) * CHUNK]
            for seg in self.segmenter.push(chunk, self.vad.prob(chunk)):
                self._utt += 1
                await self._queue.put(seg)
        self._pending = buf[n * CHUNK:]
        self._maybe_partial()

    # --- Zwischenergebnis (grau) ------------------------------------------------
    def _maybe_partial(self) -> None:
        s = self.settings
        if not s.live_partials or self._partial_busy or not self.segmenter.speaking:
            return
        if time.monotonic() - self._last_partial < s.live_partial_interval_s:
            return
        if self._queue.qsize() > 0:  # feste Sätze haben Vorrang
            return
        cur = self.segmenter.current()
        if not cur or len(cur[1]) < int(0.8 * SAMPLE_RATE):
            return
        self._partial_busy = True
        self._last_partial = time.monotonic()
        asyncio.create_task(self._partial(self._utt, cur[0], cur[1].copy()))

    async def _partial(self, utt: int, start: int, audio: np.ndarray) -> None:
        loop = asyncio.get_running_loop()
        try:
            res = await loop.run_in_executor(asr_executor(self.settings.asr_workers),
                                             self.transcriber.backend.transcribe, audio, self.settings.language)
            if utt == self._utt and not self._stopped and res.text.strip():
                await self.send({"type": "partial", "start": round(start / SAMPLE_RATE, 2), "text": res.text.strip()})
        except Exception:
            log.exception("Zwischenergebnis fehlgeschlagen")
        finally:
            self._partial_busy = False

    # --- Pause ------------------------------------------------------------------
    async def pause(self) -> None:
        """Pause: angefangenen Satz abschließen. Die Oberfläche schickt bis zum Weiter kein Audio."""
        if self.transcript is None or self._stopped:
            return
        if len(self._pending):
            chunk = np.pad(self._pending, (0, CHUNK - len(self._pending)))
            self._pending = np.zeros(0, dtype=np.float32)
            for seg in self.segmenter.push(chunk, self.vad.prob(chunk)):
                self._utt += 1
                await self._queue.put(seg)
        for seg in self.segmenter.flush():
            self._utt += 1
            await self._queue.put(seg)
        self.vad.reset()
        await self.send({"type": "paused"})

    # --- Stopp ------------------------------------------------------------------
    async def stop(self) -> dict:
        """Aufnahme beenden, Ergebnis speichern und zurückgeben."""
        if self.transcript is None or self._stopped:
            return {}
        self._stopped = True
        if len(self._pending):
            chunk = np.pad(self._pending, (0, CHUNK - len(self._pending)))
            for seg in self.segmenter.push(chunk, self.vad.prob(chunk)):
                await self._queue.put(seg)
            self._pending = np.zeros(0, dtype=np.float32)
        for seg in self.segmenter.flush():
            await self._queue.put(seg)
        await self._queue.put(None)
        if self._worker:
            await self._worker
        if self._clean_tasks:
            await asyncio.wait(self._clean_tasks, timeout=20)
        self._raw.close()
        self._raw = None

        wav_path = self.settings.audio_dir / f"{self.transcript.id}.wav"
        raw = self._raw_path.read_bytes()
        write_wav_16k(wav_path, raw)
        self._raw_path.unlink(missing_ok=True)
        duration = self._samples / SAMPLE_RATE

        refine = self.settings.live_final_pass and duration >= 1.0
        segments = self._segments
        self.store.replace_segments(self.transcript.id, segments)
        self.store.update_transcript(
            self.transcript.id, status="refining" if refine else "done", audio_path=str(wav_path),
            duration=round(duration, 2), processing_seconds=round(self._compute, 2),
        )
        if self.on_saved:
            self.on_saved(self.transcript.id, refine)
        result = {
            "type": "final",
            "transcript_id": self.transcript.id,
            "duration": round(duration, 2),
            "processing_seconds": round(self._compute, 2),
            "refining": refine,
            "segments": [seg.__dict__ for seg in segments],
        }
        await self.send(result)
        return result

    async def abort(self) -> None:
        """Verbindung abgebrochen: so viel wie möglich retten (geschützt gegen Abbruch)."""
        if self.transcript is None:
            return
        try:
            await asyncio.shield(self.stop())
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Abbruch-Sicherung fehlgeschlagen")
            self.store.update_transcript(self.transcript.id, status="error", error="Verbindung abgebrochen")

    # --- Hintergrund: feste Sätze transkribieren --------------------------------
    async def _work(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            seg = await self._queue.get()
            if seg is None:
                break
            await self.send({"type": "status", "state": "transcribing", "pending": self._queue.qsize()})
            t0 = time.perf_counter()
            try:
                utt = await loop.run_in_executor(
                    asr_executor(self.settings.asr_workers),
                    self.transcriber.backend.transcribe, seg.audio, self.settings.language,
                )
            except Exception as e:
                log.exception("Transkription eines Segments fehlgeschlagen")
                await self.send({"type": "error", "message": f"Transkription fehlgeschlagen: {e}"})
                continue
            self._compute += time.perf_counter() - t0
            if not utt.text.strip():
                await self.send({"type": "segment_empty"})
                continue
            segment = self.transcriber.utterance_to_segment(self._idx, seg, utt)
            self._idx += 1
            self._segments.append(segment)
            self.store.append_segment(self.transcript.id, segment)
            await self.send({"type": "segment", **segment.__dict__,
                             "compute_ms": int((time.perf_counter() - t0) * 1000)})
            await self.send({"type": "status", "state": "listening", "pending": self._queue.qsize()})
            if self.llm is not None:
                task = asyncio.create_task(self._clean(segment))
                self._clean_tasks.add(task)
                task.add_done_callback(self._clean_tasks.discard)

    # --- KI-Bereinigung je Satz ----------------------------------------------------
    async def _clean(self, segment: Segment) -> None:
        loop = asyncio.get_running_loop()
        ctx = [s for s in self._segments if s.idx < segment.idx][-3:]
        try:
            cleaned = await loop.run_in_executor(
                None, bereinigung.clean_segments, self.llm, [segment], self.transcriber.glossar_entries(), ctx)
        except Exception as e:
            log.warning("Live-Bereinigung fehlgeschlagen: %s", e)
            return
        changes, _, _ = bereinigung.apply_cleaned([segment], cleaned)
        for ch in changes:
            if ch["ok"] and ch["clean"] != segment.text:
                segment.clean = ch["clean"]
                self.store.update_segment(self.transcript.id, segment.idx, clean=segment.clean)
                await self.send({"type": "clean", "idx": segment.idx, "text": segment.clean})
