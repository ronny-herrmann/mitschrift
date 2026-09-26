"""FastAPI-Anwendung: REST-API, Live-WebSocket und statische Web-Oberfläche."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import auth
from .asr import create_backend
from .config import settings
from .export import to_docx, to_markdown, to_srt, to_txt
from .jobs import JobQueue
from .live import LiveSession
from .pipeline import Transcriber
from .protokoll import STYLES, LLMClient, bereinigt, build_prompt, create_protokoll, verify
from .store import Store

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mitschrift")

STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_UPLOAD = {".wav", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".opus", ".webm", ".flac", ".wma", ".mkv", ".mov"}


class State:
    store: Store
    transcriber: Transcriber
    jobs: JobQueue
    model_info: dict
    startup_seconds: float


state = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.perf_counter()
    state.store = Store(settings.db_path)
    if not auth.enabled():
        log.warning("ACCESS_PASSWORD ist leer – keine Anmeldung! Nur für localhost geeignet.")
    try:
        backend = create_backend(settings)
    except Exception as e:
        log.error(
            "Spracherkennung (%s) konnte nicht geladen werden: %s\n"
            "  → Erster Start braucht einmalig Internet für den Modell-Download (github.com bzw. huggingface.co), "
            "oder Modelle mit `python scripts/download_models.py` auf einem anderen Rechner laden und den Ordner "
            "%s hierher kopieren. Zum Testen der Oberfläche ohne Modell: ASR_BACKEND=fake.",
            settings.asr_backend, e, settings.models_dir.resolve(),
        )
        raise
    log.info("Warmlaufen des Live-Modells …")
    try:
        backend.warmup()
    except Exception:
        log.exception("Warmup fehlgeschlagen (weiter ohne)")
    final_name = (settings.final_asr_backend or "").lower()
    final_factory = None
    if final_name and final_name != settings.asr_backend.lower():
        final_factory = lambda: create_backend(settings, final_name)  # noqa: E731
    state.transcriber = Transcriber(backend, settings, glossar=lambda: state.store.get_setting("glossar", []),
                                    final_factory=final_factory)
    state.model_info = backend.info()
    state.jobs = JobQueue(state.transcriber, state.store, settings)
    state.jobs.resume_unfinished()
    state.startup_seconds = round(time.perf_counter() - t0, 1)
    log.info("Bereit nach %.1fs – %s", state.startup_seconds, state.model_info)
    cleanup_task = asyncio.create_task(_retention_loop())
    yield
    cleanup_task.cancel()
    state.store.close()


app = FastAPI(title="Mitschrift", version=__version__, lifespan=lifespan)

# --- Anmeldung ---------------------------------------------------------------------
async def require_auth(request: Request):
    if not auth.valid_token(request.cookies.get(auth.COOKIE)):
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich")


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: LoginBody, request: Request):
    client = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    if auth.locked(client):
        raise HTTPException(429, "Zu viele Fehlversuche – bitte 15 Minuten warten.")
    if not auth.enabled() or not auth.check_password(body.password, client):
        await asyncio.sleep(1.0)
        raise HTTPException(401, "Passwort falsch")
    resp = Response(status_code=204)
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=settings.session_hours * 3600, httponly=True,
                    samesite="strict", secure=settings.cookie_secure, path="/")
    return resp


@app.post("/api/logout")
async def logout():
    resp = Response(status_code=204)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# --- Hilfsfunktionen ---------------------------------------------------------------
def _get_or_404(tid: str):
    t = state.store.get_transcript(tid)
    if not t:
        raise HTTPException(404, "Transkript nicht gefunden")
    return t


async def _retention_loop():
    while True:
        try:
            _apply_retention()
        except Exception:
            log.exception("Löschlauf fehlgeschlagen")
        await asyncio.sleep(600)


def _apply_retention():
    now = datetime.now(timezone.utc)
    for t in state.store.list_transcripts(limit=10_000):
        if t.status in ("processing", "refining"):
            continue
        created = datetime.fromisoformat(t.created_at)
        age = now - created
        if settings.retention_transcript_hours and age > timedelta(hours=settings.retention_transcript_hours):
            state.store.delete_transcript(t.id)
            log.info("Transkript %s nach Frist gelöscht", t.id)
        elif settings.retention_audio_hours and t.audio_path and age > timedelta(hours=settings.retention_audio_hours):
            state.store.delete_audio(t.id)
            log.info("Audio zu %s nach Frist gelöscht", t.id)


# --- API ----------------------------------------------------------------------------
@app.get("/api/health", dependencies=[Depends(require_auth)])
async def health():
    return {
        "app": settings.app_name,
        "version": __version__,
        "model": state.model_info,
        "models": state.transcriber.info(),
        "startup_seconds": state.startup_seconds,
        "jobs_pending": state.jobs.pending,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model if settings.llm_configured else "",
        "live_final_pass": settings.live_final_pass,
        "retention": {"audio_hours": settings.retention_audio_hours, "transcript_hours": settings.retention_transcript_hours,
                      "audio_delete_after_done": settings.audio_delete_after_done},
        "auth": auth.enabled(),
        "styles": STYLES,
    }


@app.get("/api/transcripts", dependencies=[Depends(require_auth)])
async def list_transcripts():
    items = []
    for t in state.store.list_transcripts():
        d = t.to_dict(with_segments=False)
        p = state.jobs.progress(t.id)
        if p:
            d["progress"] = {"done": p[0], "total": p[1]}
        items.append(d)
    return items


@app.get("/api/transcripts/{tid}", dependencies=[Depends(require_auth)])
async def get_transcript(tid: str):
    t = _get_or_404(tid)
    d = t.to_dict()
    p = state.jobs.progress(tid)
    if p:
        d["progress"] = {"done": p[0], "total": p[1]}
    d["protokoll"] = state.store.get_protokoll(tid)
    return d


class TranscriptPatch(BaseModel):
    title: str | None = None


@app.patch("/api/transcripts/{tid}", dependencies=[Depends(require_auth)])
async def patch_transcript(tid: str, body: TranscriptPatch):
    _get_or_404(tid)
    if body.title is not None:
        state.store.update_transcript(tid, title=body.title.strip() or "Ohne Titel")
    return {"ok": True}


class SegmentPatch(BaseModel):
    text: str | None = None
    speaker: str | None = None


@app.patch("/api/transcripts/{tid}/segments/{idx}", dependencies=[Depends(require_auth)])
async def patch_segment(tid: str, idx: int, body: SegmentPatch):
    _get_or_404(tid)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not state.store.update_segment(tid, idx, **fields):
        raise HTTPException(404, "Segment nicht gefunden")
    return {"ok": True}


class SpeakerRename(BaseModel):
    von: str
    zu: str


@app.post("/api/transcripts/{tid}/speakers/rename", dependencies=[Depends(require_auth)])
async def rename_speaker(tid: str, body: SpeakerRename):
    _get_or_404(tid)
    return {"geaendert": state.store.rename_speaker(tid, body.von, body.zu.strip())}


@app.delete("/api/transcripts/{tid}", dependencies=[Depends(require_auth)])
async def delete_transcript(tid: str):
    if not state.store.delete_transcript(tid):
        raise HTTPException(404, "Transkript nicht gefunden")
    return {"ok": True}


@app.delete("/api/transcripts/{tid}/audio", dependencies=[Depends(require_auth)])
async def delete_audio(tid: str):
    _get_or_404(tid)
    state.store.delete_audio(tid)
    return {"ok": True}


@app.get("/api/transcripts/{tid}/audio", dependencies=[Depends(require_auth)])
async def get_audio(tid: str):
    t = _get_or_404(tid)
    if not t.audio_path or not Path(t.audio_path).exists():
        raise HTTPException(404, "Kein Audio (gelöscht oder nicht vorhanden)")
    return FileResponse(t.audio_path)


@app.post("/api/upload", dependencies=[Depends(require_auth)])
async def upload(file: UploadFile = File(...), title: str = Form("")):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD:
        raise HTTPException(400, f"Dateityp {suffix or '(ohne Endung)'} wird nicht unterstützt")
    t = state.store.create_transcript(
        title=title.strip() or Path(file.filename).stem, source="upload", status="processing",
        model=state.transcriber.backend.name, language=settings.language,
    )
    dest = settings.audio_dir / f"{t.id}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f, length=1024 * 1024)
    state.store.update_transcript(t.id, audio_path=str(dest))
    state.jobs.enqueue(t.id)
    return {"id": t.id, "title": t.title, "status": "processing"}


@app.get("/api/transcripts/{tid}/export", dependencies=[Depends(require_auth)])
async def export(tid: str, format: str = "txt", zeit: bool = True, sprecher: bool = True, protokoll: bool = False):
    t = _get_or_404(tid)
    safe = "".join(c for c in t.title if c.isalnum() or c in " _-").strip() or "transkript"
    if format == "txt":
        return PlainTextResponse(to_txt(t, zeit, sprecher), headers={"Content-Disposition": f'attachment; filename="{safe}.txt"'})
    if format == "md":
        return PlainTextResponse(to_markdown(t), media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="{safe}.md"'})
    if format == "srt":
        return PlainTextResponse(to_srt(t), headers={"Content-Disposition": f'attachment; filename="{safe}.srt"'})
    if format == "docx":
        pm = None
        if protokoll:
            p = state.store.get_protokoll(tid)
            pm = (p or {}).get("content", {}).get("protokoll_md")
        data = to_docx(t, pm)
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'})
    raise HTTPException(400, "format muss txt, md, srt oder docx sein")


@app.get("/api/transcripts/{tid}/nova-prompt", dependencies=[Depends(require_auth)])
async def nova_prompt(tid: str, style: str = "ergebnis"):
    t = _get_or_404(tid)
    return PlainTextResponse(build_prompt(t, style))


class ProtokollRequest(BaseModel):
    style: str = "ergebnis"


@app.post("/api/transcripts/{tid}/protokoll", dependencies=[Depends(require_auth)])
async def make_protokoll(tid: str, body: ProtokollRequest):
    t = _get_or_404(tid)
    if not settings.llm_configured:
        raise HTTPException(409, "Keine Protokoll-KI konfiguriert (LLM_BASE_URL/LLM_MODEL). Nutze „In NOVA öffnen“.")
    if not t.segments:
        raise HTTPException(400, "Transkript ist leer")
    llm = LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, create_protokoll, t, body.style, llm)
    except Exception as e:
        log.exception("Protokollerstellung fehlgeschlagen")
        raise HTTPException(502, f"Protokoll-KI-Fehler: {e}")
    return state.store.save_protokoll(tid, body.style, "entwurf", content)


class ProtokollImport(BaseModel):
    style: str = "ergebnis"
    protokoll_md: str


@app.post("/api/transcripts/{tid}/protokoll/import", dependencies=[Depends(require_auth)])
async def import_protokoll(tid: str, body: ProtokollImport):
    """Von NOVA (oder anderswo) erzeugtes Protokoll einfügen und prüfen lassen."""
    t = _get_or_404(tid)
    md = body.protokoll_md.strip()
    if not md:
        raise HTTPException(400, "Leeres Protokoll")
    content = {"style": body.style, "protokoll_md": md, "pruefung": verify(t, md), "llm": {"model": "extern (eingefügt)"}}
    return state.store.save_protokoll(tid, body.style, "entwurf", content)


class ProtokollPatch(BaseModel):
    protokoll_md: str | None = None
    status: str | None = None  # entwurf | bestaetigt


@app.patch("/api/transcripts/{tid}/protokoll", dependencies=[Depends(require_auth)])
async def patch_protokoll(tid: str, body: ProtokollPatch):
    t = _get_or_404(tid)
    p = state.store.get_protokoll(tid)
    if not p:
        raise HTTPException(404, "Kein Protokoll vorhanden")
    content = p["content"]
    if body.protokoll_md is not None:
        content["protokoll_md"] = body.protokoll_md
        content["pruefung"] = verify(t, body.protokoll_md)
    status = body.status or p["status"]
    if status not in ("entwurf", "bestaetigt"):
        raise HTTPException(400, "status muss entwurf oder bestaetigt sein")
    return state.store.save_protokoll(tid, p["style"], status, content, pid=p["id"])


@app.get("/api/transcripts/{tid}/bereinigt", dependencies=[Depends(require_auth)])
async def get_bereinigt(tid: str):
    t = _get_or_404(tid)
    return [{"idx": s.idx, "start": s.start, "speaker": s.speaker, "text": bereinigt(s.text)} for s in t.segments]


# --- Glossar ------------------------------------------------------------------------
class GlossarBody(BaseModel):
    eintraege: list[dict]


@app.get("/api/glossar", dependencies=[Depends(require_auth)])
async def get_glossar():
    return state.store.get_setting("glossar", [])


@app.put("/api/glossar", dependencies=[Depends(require_auth)])
async def put_glossar(body: GlossarBody):
    clean = [{"von": str(e.get("von", "")).strip(), "zu": str(e.get("zu", "")).strip()} for e in body.eintraege]
    clean = [e for e in clean if e["von"] and e["zu"]]
    state.store.set_setting("glossar", clean)
    return clean


# --- Live-WebSocket --------------------------------------------------------------------
def _on_live_saved(tid: str, refine: bool) -> None:
    if refine:
        state.jobs.enqueue(tid)
    elif settings.audio_delete_after_done:
        state.store.delete_audio(tid)


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    if not auth.valid_token(ws.cookies.get(auth.COOKIE)):
        await ws.close(code=4401)
        return
    await ws.accept()
    title = ws.query_params.get("title", "")

    async def send(msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    session = LiveSession(state.transcriber, state.store, settings, send, on_saved=_on_live_saved)
    try:
        await session.start(title)
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if msg.get("bytes"):
                await session.feed(msg["bytes"])
            elif msg.get("text"):
                text = msg["text"]
                if text.startswith("{") and '"stop"' in text:
                    await session.stop()
                    break
    except WebSocketDisconnect:
        log.info("Live-Verbindung getrennt – sichere Aufnahme")
        await session.abort()
    except Exception as e:
        log.exception("Live-Fehler")
        await send({"type": "error", "message": str(e)})
        await session.abort()
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# --- Web-Oberfläche -------------------------------------------------------------------
@app.get("/")
async def index(request: Request):
    if not auth.valid_token(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
async def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/sw.js")
async def service_worker():
    # Muss unter / liegen, damit der Service Worker die ganze App abdeckt (PWA-Installation)
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
