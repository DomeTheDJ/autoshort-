"""Web-Server: Login, Upload, Status, Vorschau und Download."""
from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import shutil
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.concurrency import run_in_threadpool

from app.config import Settings, load_settings
from app.costs import CostTracker
from app.errors import UserError
from app.jobs import JobStore, Worker, start_cleanup_thread
from app.pipeline import media
from app.pipeline import run as pipeline

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("autoshorts")

STATIC = Path(__file__).parent / "static"
COOKIE = "as_session"
SESSION_DAYS = 7
ALLOWED_EXT = {".mp4", ".mov", ".m4v"}
FILE_RE = re.compile(r"^(short_[1-9][0-9]?\.(mp4|jpg)|shorts\.zip)$")
MAX_ACTIVE_JOBS = 3


def create_app(settings: Settings | None = None, processor=None, start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    secret = settings.secret_key or secrets.token_hex(32)
    signer = TimestampSigner(secret, salt="autoshorts-session")
    store = JobStore(settings)
    tracker = CostTracker(settings.data_dir / "costs.json", settings.daily_budget_usd)
    worker = Worker(store, settings, processor or pipeline.process, tracker)
    failed_logins: dict[str, deque] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.app_password:
            log.warning("APP_PASSWORD ist nicht gesetzt – Login ist deaktiviert!")
        store.mark_interrupted()
        if start_worker:
            worker.start()
            start_cleanup_thread(store, settings.retention_hours)
        yield
        worker.stop()

    app = FastAPI(title="AutoShorts", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.worker, app.state.tracker = settings, store, worker, tracker

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(UserError)
    async def user_error(_: Request, exc: UserError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "")
        return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")

    def require_auth(request: Request) -> None:
        token = request.cookies.get(COOKIE)
        if not token or not settings.app_password:
            raise HTTPException(401, "Bitte anmelden.")
        try:
            signer.unsign(token, max_age=SESSION_DAYS * 86400)
        except (BadSignature, SignatureExpired):
            raise HTTPException(401, "Sitzung abgelaufen. Bitte erneut anmelden.")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        ip = client_ip(request)
        attempts = failed_logins[ip]
        now = time.time()
        while attempts and attempts[0] < now - 900:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Zu viele Fehlversuche. Bitte 15 Minuten warten.")
        if not settings.app_password:
            raise HTTPException(503, "Auf dem Server ist kein Passwort gesetzt (APP_PASSWORD). "
                                     "Bitte in Render unter ‚Environment‘ eintragen.")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        password = str(body.get("password", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(password.encode(), settings.app_password.encode()):
            attempts.append(now)
            raise HTTPException(401, "Falsches Passwort.")
        attempts.clear()
        token = signer.sign(secrets.token_hex(8)).decode()
        response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                            secure=settings.secure_cookies, samesite="strict")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me", dependencies=[Depends(require_auth)])
    def me():
        return {"ok": True, "max_upload_mb": settings.max_upload_mb,
                "max_video_minutes": settings.max_video_minutes,
                "music": pipeline.list_music(settings),
                "transcription": settings.use_openai(), "ai": settings.use_claude(),
                "min_clips": settings.min_clips, "max_clips": settings.max_clips,
                "budget": {"limit": settings.daily_budget_usd, "spent": round(tracker.spent_today(), 3)}}

    def _bool(v: str | None, default: bool) -> bool:
        return default if v is None else v.lower() in ("1", "true", "yes", "on")

    @app.post("/api/upload", dependencies=[Depends(require_auth)])
    async def upload(request: Request):
        q = request.query_params
        filename = Path(q.get("filename", "video.mp4")).name[:120] or "video.mp4"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            raise UserError("Nur MP4- oder MOV-Videos sind erlaubt.")
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes:
            raise HTTPException(413, f"Die Datei ist zu groß (max. {settings.max_upload_mb} MB).")
        active = [j for j in store.list() if j["status"] in ("queued", "running")]
        if len(active) >= MAX_ACTIVE_JOBS:
            raise UserError("Es laufen bereits mehrere Videos. Bitte warten, bis eines fertig ist.")
        free = shutil.disk_usage(settings.data_dir).free
        if length and length.isdigit() and int(length) * 2.5 > free:
            raise UserError("Auf dem Server ist nicht genug Speicherplatz frei.")

        try:
            clips = int(q.get("clips", settings.max_clips))
        except ValueError:
            clips = settings.max_clips
        options = {
            "clips": max(1, min(settings.max_clips, clips)),
            "subtitles": _bool(q.get("subtitles"), True),
            "remove_pauses": _bool(q.get("remove_pauses"), True),
            "zooms": _bool(q.get("zooms"), True),
            "sfx": _bool(q.get("sfx"), True),
            "music": q.get("music") or None,
        }
        job_id = store.new_id()
        d = store.dir(job_id)
        d.mkdir(parents=True)
        part = d / f"upload{ext}.part"
        size = 0
        try:
            with open(part, "wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, f"Die Datei ist zu groß (max. {settings.max_upload_mb} MB).")
                    fh.write(chunk)
            if size == 0:
                raise UserError("Die Datei ist leer.")
            original = d / f"original{ext}"
            part.rename(original)
            original.chmod(0o400)  # schreibgeschützt: wird nur gelesen, nie verändert
            await run_in_threadpool(media.probe, original)
        except BaseException:
            shutil.rmtree(d, ignore_errors=True)
            raise
        store.create(job_id, filename, options)
        worker.submit(job_id)
        return {"id": job_id}

    def public(job: dict) -> dict:
        keep = ("id", "status", "progress", "message", "filename", "created", "error", "result", "options")
        return {k: job.get(k) for k in keep}

    @app.get("/api/jobs", dependencies=[Depends(require_auth)])
    def jobs():
        return [public(j) for j in store.list()]

    def _job(job_id: str) -> dict:
        try:
            job = store.get(job_id)
        except KeyError:
            job = None
        if job is None:
            raise HTTPException(404, "Auftrag nicht gefunden (evtl. schon automatisch gelöscht).")
        return job

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
    def job(job_id: str):
        return public(_job(job_id))

    @app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
    def delete_job(job_id: str):
        j = _job(job_id)
        if j["status"] == "running":
            raise UserError("Ein laufender Auftrag kann nicht gelöscht werden.")
        store.delete(job_id)
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/files/{name}", dependencies=[Depends(require_auth)])
    def job_file(job_id: str, name: str, dl: int = 0):
        j = _job(job_id)
        if j["status"] != "done" or not FILE_RE.match(name):
            raise HTTPException(404, "Datei nicht gefunden.")
        path = store.dir(job_id) / "output" / name
        if not path.is_file():
            raise HTTPException(404, "Datei nicht gefunden.")
        media_type = {"mp4": "video/mp4", "jpg": "image/jpeg", "zip": "application/zip"}[name.rsplit(".", 1)[1]]
        headers = {"Cache-Control": "private, no-store"}
        disp = "attachment" if (dl or name.endswith(".zip")) else "inline"
        return FileResponse(path, media_type=media_type, headers=headers,
                            filename=name, content_disposition_type=disp)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app() if os.environ.get("AUTOSHORTS_NO_APP") != "1" else None
