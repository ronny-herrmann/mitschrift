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
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import auth, bereinigung
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
    transcriber: Transcriber | None = None
    jobs: JobQueue | None = None
    model_info: dict = {}
    startup_seconds: float = 0.0
    load_status: str = "lädt"      # lädt | bereit | fehler
    load_error: str = ""
    tasks: dict = {}               # tid → laufende/letzte KI-Aufgabe (Bereinigen, Protokoll)


state = State()


def _korrekturen(tid: str) -> list[dict]:
    return state.store.get_setting(f"korrekturen:{tid}", [])


def _start_task(tid: str, kind: str, fn) -> dict:
    """KI-Aufgabe im Hintergrund starten; Fortschritt über GET /api/transcripts/{tid}/task."""
    import threading
    cur = state.tasks.get(tid)
    if cur and cur["status"] == "running":
        raise HTTPException(409, "Für dieses Transkript läuft bereits eine KI-Aufgabe.")
    task = {"kind": kind, "status": "running", "done": 0, "total": 0, "started": time.time(), "result": None, "error": ""}
    state.tasks[tid] = task

    def progress(done: int, total: int) -> None:
        task["done"], task["total"] = done, total

    def run() -> None:
        try:
            task["result"] = fn(progress)
            task["status"] = "done"
        except ValueError as e:
            task["status"], task["error"] = "error", str(e)
        except Exception as e:
            log.exception("KI-Aufgabe %s fehlgeschlagen", kind)
            task["status"], task["error"] = "error", f"KI-Fehler: {e}"
        task["seconds"] = round(time.time() - task["started"], 1)

    threading.Thread(target=run, name=f"task-{kind}", daemon=True).start()
    return {"started": True, "kind": kind}


def get_llm() -> LLMClient | None:
    if not settings.llm_configured:
        return None
    return LLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)


def _load_models() -> None:
    """Modell im Hintergrund laden – die Oberfläche ist sofort erreichbar und zeigt „Modell lädt …“.
    Bei Fehlern (z. B. Netz beim ersten Download) wird mehrfach neu versucht."""
    for attempt in range(1, 6):
        _load_models_once()
        if state.load_status != "fehler":
            return
        if attempt < 5:
            log.warning("Neuer Ladeversuch in %ss (%s/5)", 15 * attempt, attempt + 1)
            time.sleep(15 * attempt)
            state.load_status, state.load_error = "lädt", ""


def _load_models_once() -> None:
    t0 = time.perf_counter()
    try:
        backend = create_backend(settings)
        log.info("Warmlaufen des Live-Modells …")
        try:
            backend.warmup()
        except Exception:
            log.exception("Warmup fehlgeschlagen (weiter ohne)")
        final_name = (settings.final_asr_backend or "").lower()
        final_factory = None
        if final_name and final_name != settings.asr_backend.lower():
            final_factory = lambda: create_backend(settings, final_name)  # noqa: E731
        transcriber = Transcriber(backend, settings, glossar=lambda: state.store.get_setting("glossar", []),
                                  final_factory=final_factory)
        state.model_info = backend.info()
        state.jobs = JobQueue(transcriber, state.store, settings, llm_factory=get_llm)
        state.transcriber = transcriber
        state.jobs.resume_unfinished()
        state.startup_seconds = round(time.perf_counter() - t0, 1)
        state.load_status = "bereit"
        log.info("Bereit nach %.1fs – %s", state.startup_seconds, state.model_info)
    except Exception as e:
        state.load_status = "fehler"
        state.load_error = str(e)
        log.error(
            "Spracherkennung (%s) konnte nicht geladen werden: %s\n"
            "  → Erster Start braucht einmalig Internet für den Modell-Download (github.com), "
            "oder den Ordner %s von einem anderen Rechner kopieren. Oberflächentest ohne Modell: ASR_BACKEND=fake.",
            settings.asr_backend, e, settings.models_dir.resolve(),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.store = Store(settings.db_path)
    if not auth.enabled():
        log.warning("ACCESS_PASSWORD ist leer – keine Anmeldung! Nur für localhost geeignet.")
    import threading
    threading.Thread(target=_load_models, name="load-models", daemon=True).start()
    cleanup_task = asyncio.create_task(_retention_loop())
    yield
    cleanup_task.cancel()
    state.store.close()


def require_ready():
    if state.transcriber is None:
        raise HTTPException(503, "Das Sprachmodell lädt noch – bitte einen Moment warten." if state.load_status == "lädt"
                            else f"Sprachmodell nicht verfügbar: {state.load_error}")


app = FastAPI(title="Protokollant", version=__version__, lifespan=lifespan)

# --- Sicherheits-Header ------------------------------------------------------------
# Content-Security-Policy: nur eigene Skripte (keine Inline-Skripte, keine fremden Quellen).
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; worker-src 'self'; font-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


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
DOCS_DIR = Path(__file__).parent / "docs"
DOCS = {"faq", "technik", "infrastruktur", "datenschutz", "sicherheit", "personalrat"}


@app.get("/api/docs/{name}", dependencies=[Depends(require_auth)])
async def get_doc(name: str):
    """Interne Info-Seiten (nur angemeldet). Inhalte liegen als HTML-Bausteine in app/docs/."""
    if name not in DOCS:
        raise HTTPException(404, "Unbekannte Seite")
    return HTMLResponse((DOCS_DIR / f"{name}.html").read_text(encoding="utf-8"))


@app.post("/api/llm/test", dependencies=[Depends(require_auth)])
def llm_test():
    """Misst das Sprachmodell mit der echten Bereinigung (gleiche Anweisung und Treue-Prüfung wie im Betrieb)."""
    llm = get_llm()
    if llm is None:
        raise HTTPException(400, "Kein Sprachmodell konfiguriert")
    from .bereinigung import clean_segments, fidelity
    from .store import Segment

    saetze = [
        "ähm ja also wir haben ähm die Tabelle im CHS gepflegt und die ist jetzt fertig",
        "genau und äh am Montag schicken wir das an die Kämmerei raus",
        "der Haushaltsentwurf 2027 wird dann im Ausschuss äh vorgestellt",
        "Frau Müller übernimmt die die Nachfrage bis Freitag",
        "gut dann machen wir das so",
    ]
    segs = [Segment(i, float(i * 5), float(i * 5 + 4), t, "") for i, t in enumerate(saetze)]
    t0 = time.perf_counter()
    try:
        cleaned = clean_segments(llm, segs, [{"von": "CHS", "zu": "Excel"}])
    except Exception as e:
        raise HTTPException(503, f"Sprachmodell nicht erreichbar oder lädt noch: {e}")
    secs = round(time.perf_counter() - t0, 2)
    zeilen = []
    for s in segs:
        neu = cleaned.get(s.idx, "")
        ok, grund = fidelity(s.text, neu) if neu else (False, "fehlt")
        zeilen.append({"original": s.text, "bereinigt": neu, "ok": ok, "grund": grund})
    return {"sekunden": secs, "zeilen": zeilen, "modell": settings.llm_model,
            "tokens_pro_sekunde_ausgabe": None, "tokens_pro_sekunde_eingabe": None}


@app.get("/api/health", dependencies=[Depends(require_auth)])
async def health():
    return {
        "app": settings.app_name,
        "version": __version__,
        "ready": state.transcriber is not None,
        "load_status": state.load_status,
        "load_error": state.load_error,
        "model": state.model_info,
        "models": state.transcriber.info() if state.transcriber else {},
        "startup_seconds": state.startup_seconds,
        "jobs_pending": state.jobs.pending if state.jobs else 0,
        "diarization": settings.diarization,
        "live_ai_clean": settings.live_ai_clean and settings.auto_ai_clean and settings.llm_configured,
        "auto_ai_clean": settings.auto_ai_clean and settings.llm_configured,
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
        p = state.jobs.progress(t.id) if state.jobs else None
        if p:
            d["progress"] = {"done": p[0], "total": p[1]}
        items.append(d)
    return items


@app.get("/api/transcripts/{tid}", dependencies=[Depends(require_auth)])
async def get_transcript(tid: str):
    t = _get_or_404(tid)
    d = t.to_dict()
    p = state.jobs.progress(tid) if state.jobs else None
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
    clean: str | None = None


@app.patch("/api/transcripts/{tid}/segments/{idx}", dependencies=[Depends(require_auth)])
async def patch_segment(tid: str, idx: int, body: SegmentPatch):
    t = _get_or_404(tid)
    seg = next((s for s in t.segments if s.idx == idx), None)
    if seg is None:
        raise HTTPException(404, "Segment nicht gefunden")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    paare: list[dict] = []
    if "text" in fields or "clean" in fields:
        alt = seg.clean or seg.text
        neu = fields.get("clean", fields.get("text", ""))
        # Die eigene Korrektur gilt: sichtbarer Text = eingegebener Text, KI fasst das Segment nicht mehr an
        fields.update(text=neu if "text" in fields else seg.text, clean=neu if seg.clean or "clean" in fields else "",
                      clean_note="", edited=True)
        paare = bereinigung.korrektur_paare(alt, neu)
        if paare:
            bekannt = _korrekturen(tid)
            for p in paare:
                bekannt = [k for k in bekannt if k["von"].lower() != p["von"].lower()] + [p]
            state.store.set_setting(f"korrekturen:{tid}", bekannt[-50:])
    state.store.update_segment(tid, idx, **fields)
    weitere = bereinigung.weitere_stellen(t.segments, paare, idx) if paare else []
    return {"ok": True, "korrekturen": paare, "weitere_stellen": weitere}


class ReplaceBody(BaseModel):
    paare: list[dict]


@app.post("/api/transcripts/{tid}/ersetzen", dependencies=[Depends(require_auth)])
async def ersetzen(tid: str, body: ReplaceBody):
    """Eine Korrektur an allen weiteren Stellen übernehmen („Bini Kum“ → „Winnie Puuh“)."""
    from .glossar import apply_glossar, compile_glossar
    t = _get_or_404(tid)
    rules = compile_glossar([{"von": str(p.get("von", "")), "zu": str(p.get("zu", ""))} for p in body.paare])
    n = 0
    for s in t.segments:
        neu_text, neu_clean = apply_glossar(s.text, rules), apply_glossar(s.clean, rules) if s.clean else ""
        if neu_text != s.text or neu_clean != s.clean:
            state.store.update_segment(tid, s.idx, text=neu_text, clean=neu_clean, edited=True)
            n += 1
    return {"ersetzt": n}


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
    require_ready()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD:
        raise HTTPException(400, f"Dateityp {suffix or '(ohne Endung)'} wird nicht unterstützt")
    t = state.store.create_transcript(
        title=title.strip() or Path(file.filename).stem, source="upload", status="processing",
        model=state.transcriber.final_backend.info().get("model", ""), language=settings.language,
    )
    dest = settings.audio_dir / f"{t.id}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f, length=1024 * 1024)
    state.store.update_transcript(t.id, audio_path=str(dest))
    state.jobs.enqueue(t.id)
    return {"id": t.id, "title": t.title, "status": "processing"}


def _download_headers(title: str, suffix: str, ext: str) -> dict:
    from urllib.parse import quote
    base = "".join(c for c in title if c.isalnum() or c in " _-.").strip() or "Transkript"
    name = f"{base} - {suffix}.{ext}"
    ascii_name = name.encode("ascii", "ignore").decode() or f"transkript.{ext}"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"}


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/transcripts/{tid}/export", dependencies=[Depends(require_auth)])
async def export(tid: str, format: str = "txt", zeit: bool = True, sprecher: bool = True, protokoll: bool = False):
    t = _get_or_404(tid)
    if format == "txt":
        suffix = "Transkript mit Zeit und Sprecher" if (zeit and sprecher) else "Fliesstext"
        return PlainTextResponse(to_txt(t, zeit, sprecher), headers=_download_headers(t.title, suffix, "txt"))
    if format == "md":
        return PlainTextResponse(to_markdown(t), media_type="text/markdown", headers=_download_headers(t.title, "Markdown", "md"))
    if format == "srt":
        return PlainTextResponse(to_srt(t), headers=_download_headers(t.title, "Untertitel", "srt"))
    if format in ("docx", "fliesstext"):
        pm, pname = None, ""
        if protokoll:
            p = state.store.get_protokoll(tid)
            pm = (p or {}).get("content", {}).get("protokoll_md")
            pname = STYLES.get((p or {}).get("style", ""), "Zusammenfassung")
            if not pm:
                raise HTTPException(404, "Noch keine Zusammenfassung vorhanden")
        if format == "fliesstext":
            data, suffix = to_docx(t, fliesstext=True), "Fliesstext"
        else:
            data = to_docx(t, pm, pname)
            suffix = f"Transkript und {pname}" if pm else "Transkript"
        return Response(data, media_type=DOCX_TYPE, headers=_download_headers(t.title, suffix, "docx"))
    raise HTTPException(400, "format muss txt, md, srt, docx oder fliesstext sein")


@app.get("/api/transcripts/{tid}/nova-prompt", dependencies=[Depends(require_auth)])
async def nova_prompt(tid: str, style: str = "zusammenfassung"):
    t = _get_or_404(tid)
    return PlainTextResponse(build_prompt(t, style))


class ProtokollRequest(BaseModel):
    style: str = "zusammenfassung"


@app.post("/api/transcripts/{tid}/protokoll", dependencies=[Depends(require_auth)])
async def make_protokoll(tid: str, body: ProtokollRequest):
    """Startet die Protokollerstellung im Hintergrund. Fortschritt: GET …/task, Ergebnis: GET …/{tid}."""
    t = _get_or_404(tid)
    if not settings.llm_configured:
        raise HTTPException(409, "Keine KI angebunden (LLM_BASE_URL/LLM_MODEL). Nutze „Prompt für NOVA kopieren“.")
    if not t.segments:
        raise HTTPException(400, "Transkript ist leer")
    style = body.style if body.style in STYLES else "zusammenfassung"

    def run(progress):
        cache = state.store.get_setting(f"extrakt:{tid}", None)
        content = create_protokoll(t, style, get_llm(), progress=progress, cache=cache)
        state.store.set_setting(f"extrakt:{tid}", content.pop("extrakt"))
        return state.store.save_protokoll(tid, style, "entwurf", content)

    res = _start_task(tid, "protokoll", run)
    state.tasks[tid]["style"] = style
    return res


@app.get("/api/transcripts/{tid}/task", dependencies=[Depends(require_auth)])
async def get_task(tid: str):
    task = state.tasks.get(tid)
    if not task:
        return {"status": "none"}
    out = {k: v for k, v in task.items() if k != "result"}
    out["elapsed"] = round(time.time() - task["started"], 1)
    if task["status"] == "done" and task["kind"] == "bereinigen":
        out["result"] = task["result"]
    return out


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


# --- KI-Bereinigung ------------------------------------------------------------------
@app.get("/api/transcripts/{tid}/bereinigen-prompt", dependencies=[Depends(require_auth)])
async def bereinigen_prompt(tid: str):
    t = _get_or_404(tid)
    return PlainTextResponse(bereinigung.build_prompt(t, state.store.get_setting("glossar", []), _korrekturen(tid)))


def _store_clean(t, cleaned: dict[int, str]) -> dict:
    segs = [s for s in t.segments if not s.edited]
    changes, ok_n, bad_n = bereinigung.apply_cleaned(segs, cleaned)
    for c in changes:
        if c["ok"]:
            state.store.update_segment(t.id, c["idx"], clean=c["clean"], clean_note="")
        else:
            state.store.update_segment(t.id, c["idx"], clean="", clean_note=c["grund"])
    missing = len([s for s in segs if s.idx not in cleaned])
    return {"uebernommen": ok_n, "verworfen": bad_n, "fehlend": missing,
            "eigene_korrekturen_behalten": len(t.segments) - len(segs),
            "verworfen_details": [c for c in changes if not c["ok"]]}


@app.post("/api/transcripts/{tid}/bereinigen", dependencies=[Depends(require_auth)])
async def bereinigen(tid: str):
    """Startet die KI-Bereinigung im Hintergrund. Von Hand korrigierte Sätze bleiben unangetastet."""
    t = _get_or_404(tid)
    llm = get_llm()
    if llm is None:
        raise HTTPException(409, "Keine KI angebunden (LLM_BASE_URL/LLM_MODEL). Nutze „Prompt für NOVA kopieren“.")
    if not t.segments:
        raise HTTPException(400, "Transkript ist leer")

    def run(progress):
        cleaned = bereinigung.clean_segments(llm, t.segments, state.store.get_setting("glossar", []),
                                             korrekturen=_korrekturen(tid), progress=progress)
        return _store_clean(t, cleaned)

    return _start_task(tid, "bereinigen", run)


class CleanImport(BaseModel):
    text: str


@app.post("/api/transcripts/{tid}/bereinigen/import", dependencies=[Depends(require_auth)])
async def bereinigen_import(tid: str, body: CleanImport):
    """Von NOVA bereinigten Text (Zeilen mit [S12]) übernehmen – mit derselben Treue-Prüfung."""
    t = _get_or_404(tid)
    cleaned = bereinigung.parse_lines(body.text)
    if not cleaned:
        raise HTTPException(400, "Keine Zeilen mit [S…]-Nummern gefunden")
    return _store_clean(t, cleaned)


@app.delete("/api/transcripts/{tid}/bereinigen", dependencies=[Depends(require_auth)])
async def bereinigen_verwerfen(tid: str):
    t = _get_or_404(tid)
    for s in t.segments:
        if not s.edited:
            state.store.update_segment(tid, s.idx, clean="", clean_note="")
    return {"ok": True}


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
    if state.transcriber is None:
        await ws.accept()
        await ws.send_json({"type": "error", "message": "Das Sprachmodell lädt noch – bitte in einigen Sekunden erneut starten."})
        await ws.close(code=4503)
        return
    await ws.accept()
    title = ws.query_params.get("title", "")

    async def send(msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    session = LiveSession(state.transcriber, state.store, settings, send, on_saved=_on_live_saved,
                          llm_factory=get_llm)
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
                if text.startswith("{") and '"pause"' in text:
                    await session.pause()
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
