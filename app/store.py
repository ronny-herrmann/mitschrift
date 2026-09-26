"""Speicherung: SQLite-Datenbank (Transkripte, Segmente, Protokolle, Einstellungen) + Audio-Dateien.

Alles liegt in DATA_DIR – kein externer Dienst, keine Cloud.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Segment:
    idx: int
    start: float
    end: float
    text: str
    speaker: str = ""
    words: list[dict] = field(default_factory=list)  # [{"w":..., "s":..., "e":...}]
    clean: str = ""       # KI-bereinigter Text (leer = nicht bereinigt)
    clean_note: str = ""  # Grund, falls die Bereinigung verworfen wurde


@dataclass
class Transcript:
    id: str
    title: str
    created_at: str
    source: str            # live | upload
    status: str            # processing | done | error
    audio_path: str = ""
    duration: float = 0.0
    model: str = ""
    language: str = "de"
    error: str = ""
    processing_seconds: float = 0.0
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self, with_segments: bool = True) -> dict:
        d = asdict(self)
        if not with_segments:
            d.pop("segments", None)
        d["has_audio"] = bool(self.audio_path) and Path(self.audio_path).exists()
        return d

    @property
    def text(self) -> str:
        return "\n".join((s.clean or s.text) for s in self.segments if s.text)


SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    audio_path TEXT DEFAULT '',
    duration REAL DEFAULT 0,
    model TEXT DEFAULT '',
    language TEXT DEFAULT 'de',
    error TEXT DEFAULT '',
    processing_seconds REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS segments (
    transcript_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    text TEXT NOT NULL,
    speaker TEXT DEFAULT '',
    words TEXT DEFAULT '[]',
    clean TEXT DEFAULT '',
    clean_note TEXT DEFAULT '',
    PRIMARY KEY (transcript_id, idx)
);
CREATE TABLE IF NOT EXISTS protokolle (
    id TEXT PRIMARY KEY,
    transcript_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    style TEXT NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._tx() as c:
            c.executescript(SCHEMA)
            cols = {r[1] for r in c.execute("PRAGMA table_info(segments)").fetchall()}
            for col in ("clean", "clean_note"):
                if col not in cols:
                    c.execute(f"ALTER TABLE segments ADD COLUMN {col} TEXT DEFAULT ''")

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # --- Transkripte -----------------------------------------------------
    def create_transcript(self, title: str, source: str, status: str = "processing", **kw) -> Transcript:
        t = Transcript(id=new_id(), title=title or "Ohne Titel", created_at=now_iso(), source=source, status=status, **kw)
        with self._tx() as c:
            c.execute(
                "INSERT INTO transcripts (id,title,created_at,source,status,audio_path,duration,model,language,error,processing_seconds)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t.id, t.title, t.created_at, t.source, t.status, t.audio_path, t.duration, t.model, t.language,
                 t.error, t.processing_seconds),
            )
        return t

    def update_transcript(self, tid: str, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._tx() as c:
            c.execute(f"UPDATE transcripts SET {cols} WHERE id=?", (*fields.values(), tid))

    def replace_segments(self, tid: str, segments: list[Segment]) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM segments WHERE transcript_id=?", (tid,))
            c.executemany(
                "INSERT INTO segments (transcript_id,idx,start,end,text,speaker,words,clean,clean_note) VALUES (?,?,?,?,?,?,?,?,?)",
                [(tid, s.idx, s.start, s.end, s.text, s.speaker, json.dumps(s.words, ensure_ascii=False), s.clean,
                  s.clean_note) for s in segments],
            )

    def append_segment(self, tid: str, seg: Segment) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO segments (transcript_id,idx,start,end,text,speaker,words,clean,clean_note)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (tid, seg.idx, seg.start, seg.end, seg.text, seg.speaker, json.dumps(seg.words, ensure_ascii=False),
                 seg.clean, seg.clean_note),
            )

    def update_segment(self, tid: str, idx: int, **fields) -> bool:
        if not fields:
            return False
        if "words" in fields:
            fields["words"] = json.dumps(fields["words"], ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._tx() as c:
            cur = c.execute(f"UPDATE segments SET {cols} WHERE transcript_id=? AND idx=?", (*fields.values(), tid, idx))
            return cur.rowcount > 0

    def rename_speaker(self, tid: str, old: str, new: str) -> int:
        with self._tx() as c:
            cur = c.execute("UPDATE segments SET speaker=? WHERE transcript_id=? AND speaker=?", (new, tid, old))
            return cur.rowcount

    def get_transcript(self, tid: str) -> Transcript | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM transcripts WHERE id=?", (tid,)).fetchone()
            if not row:
                return None
            segs = self._conn.execute(
                "SELECT * FROM segments WHERE transcript_id=? ORDER BY idx", (tid,)
            ).fetchall()
        t = self._row_to_transcript(row)
        t.segments = [
            Segment(idx=s["idx"], start=s["start"], end=s["end"], text=s["text"], speaker=s["speaker"] or "",
                    words=json.loads(s["words"] or "[]"), clean=s["clean"] or "", clean_note=s["clean_note"] or "")
            for s in segs
        ]
        return t

    def list_transcripts(self, limit: int = 200) -> list[Transcript]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transcripts ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_transcript(r) for r in rows]

    def delete_transcript(self, tid: str) -> Transcript | None:
        t = self.get_transcript(tid)
        if not t:
            return None
        with self._tx() as c:
            c.execute("DELETE FROM segments WHERE transcript_id=?", (tid,))
            c.execute("DELETE FROM protokolle WHERE transcript_id=?", (tid,))
            c.execute("DELETE FROM transcripts WHERE id=?", (tid,))
        if t.audio_path:
            p = Path(t.audio_path)
            if p.exists():
                p.unlink()
        return t

    def delete_audio(self, tid: str) -> bool:
        t = self.get_transcript(tid)
        if not t or not t.audio_path:
            return False
        p = Path(t.audio_path)
        if p.exists():
            p.unlink()
        self.update_transcript(tid, audio_path="")
        return True

    @staticmethod
    def _row_to_transcript(row: sqlite3.Row) -> Transcript:
        return Transcript(
            id=row["id"], title=row["title"], created_at=row["created_at"], source=row["source"],
            status=row["status"], audio_path=row["audio_path"] or "", duration=row["duration"] or 0.0,
            model=row["model"] or "", language=row["language"] or "de", error=row["error"] or "",
            processing_seconds=row["processing_seconds"] or 0.0,
        )

    # --- Protokolle ------------------------------------------------------
    def save_protokoll(self, tid: str, style: str, status: str, content: dict, pid: str | None = None) -> dict:
        pid = pid or new_id()
        created = now_iso()
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO protokolle (id,transcript_id,created_at,style,status,content) VALUES (?,?,?,?,?,?)",
                (pid, tid, created, style, status, json.dumps(content, ensure_ascii=False)),
            )
        return {"id": pid, "transcript_id": tid, "created_at": created, "style": style, "status": status, "content": content}

    def get_protokoll(self, tid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM protokolle WHERE transcript_id=? ORDER BY created_at DESC LIMIT 1", (tid,)
            ).fetchone()
        if not row:
            return None
        return {"id": row["id"], "transcript_id": row["transcript_id"], "created_at": row["created_at"],
                "style": row["style"], "status": row["status"], "content": json.loads(row["content"])}

    # --- Einstellungen (z. B. Glossar) ----------------------------------
    def get_setting(self, key: str, default):
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
