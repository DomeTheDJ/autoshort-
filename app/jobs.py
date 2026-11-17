"""Job-Speicher (JSON-Dateien) und Hintergrund-Worker für lange Videoverarbeitung."""
from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from app import diagnostics
from app.config import Settings
from app.costs import CostTracker
from app.errors import UserError

log = logging.getLogger(__name__)
_lock = threading.RLock()


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.jobs_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def dir(self, job_id: str) -> Path:
        if not job_id.isalnum() or len(job_id) != 32:
            raise KeyError(job_id)
        return self.root / job_id

    def create(self, job_id: str, filename: str, options: dict) -> dict:
        job = {"id": job_id, "status": "queued", "progress": 0, "message": "In Warteschlange",
               "filename": filename, "options": options, "created": time.time(),
               "updated": time.time(), "result": None, "error": None}
        self.save(job)
        return job

    def save(self, job: dict) -> None:
        with _lock:
            d = self.dir(job["id"])
            d.mkdir(parents=True, exist_ok=True)
            job["updated"] = time.time()
            tmp = d / "job.json.tmp"
            tmp.write_text(json.dumps(job, ensure_ascii=False))
            tmp.replace(d / "job.json")

    def get(self, job_id: str) -> dict | None:
        try:
            return json.loads((self.dir(job_id) / "job.json").read_text())
        except (OSError, ValueError, KeyError):
            return None

    def update(self, job_id: str, **fields) -> dict | None:
        with _lock:
            job = self.get(job_id)
            if job is None:
                return None
            job.update(fields)
            self.save(job)
            return job

    def list(self) -> list[dict]:
        jobs = []
        for d in self.root.iterdir() if self.root.exists() else []:
            j = self.get(d.name) if d.is_dir() else None
            if j:
                jobs.append(j)
        return sorted(jobs, key=lambda j: j["created"], reverse=True)

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self.dir(job_id), ignore_errors=True)

    def mark_interrupted(self) -> None:
        """Nach einem Neustart: angefangene Jobs als fehlgeschlagen markieren."""
        for j in self.list():
            if j["status"] in ("queued", "running"):
                where = f" Abbruch bei {j.get('progress', 0)} % ({j.get('message', '')})." \
                    if j["status"] == "running" else ""
                self.update(j["id"], status="failed",
                            error="Der Server wurde neu gestartet. Bitte das Video erneut hochladen." + where)

    def cleanup(self, max_age_hours: float) -> int:
        cutoff = time.time() - max_age_hours * 3600
        removed = 0
        for d in list(self.root.iterdir()) if self.root.exists() else []:
            if not d.is_dir():
                continue
            j = self.get(d.name)
            ts = j["updated"] if j else d.stat().st_mtime
            if ts < cutoff and (not j or j["status"] not in ("running",)):
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed


Processor = Callable[[Path, Path, dict, Settings, Callable[[float, str], None], CostTracker], dict]


class Worker:
    """Ein Hintergrund-Thread, der Jobs nacheinander abarbeitet (schont CPU und RAM)."""

    def __init__(self, store: JobStore, settings: Settings, processor: Processor, tracker: CostTracker):
        self.store, self.settings, self.processor, self.tracker = store, settings, processor, tracker
        self.q: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="autoshorts-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.q.put("")

    def submit(self, job_id: str) -> None:
        self.q.put(job_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self.q.get()
            if not job_id:
                continue
            self.run_one(job_id)

    def run_one(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if job is None:
            return
        d = self.store.dir(job_id)
        source = next(iter(d.glob("original.*")), None)
        self.store.update(job_id, status="running", progress=1, message="Verarbeitung startet")
        stop_sampling = threading.Event()

        def sample() -> None:
            """Alle 2 s Speicher messen und speichern – überlebt einen Absturz auf der Platte."""
            peak = 0
            while not stop_sampling.is_set():
                try:
                    snap = diagnostics.snapshot(self.settings.data_dir)
                    peak = max(peak, snap.get("memory", {}).get("used_mb") or 0)
                    video = (d / "video.txt").read_text() if (d / "video.txt").exists() else ""
                    text = diagnostics.summary(snap) + f"; höchster Messwert {peak} MB"
                    self.store.update(job_id, diag=(video + "; " if video else "") + text)
                except Exception:
                    log.debug("Diagnose fehlgeschlagen", exc_info=True)
                stop_sampling.wait(2.0)

        threading.Thread(target=sample, name="autoshorts-diag", daemon=True).start()

        def progress(pct: float, msg: str) -> None:
            self.store.update(job_id, progress=round(min(99.0, pct), 1), message=msg)

        try:
            if source is None:
                raise UserError("Die hochgeladene Datei wurde nicht gefunden.")
            result = self.processor(d, source, job["options"], self.settings, progress, self.tracker)
            self.store.update(job_id, status="done", progress=100, message="Fertig", result=result)
        except UserError as exc:
            self.store.update(job_id, status="failed", error=str(exc), message="Fehlgeschlagen")
        except Exception:
            log.exception("Job %s fehlgeschlagen", job_id)
            self.store.update(job_id, status="failed", message="Fehlgeschlagen",
                              error="Bei der Verarbeitung ist ein technischer Fehler aufgetreten. "
                                    "Bitte ein anderes Video versuchen oder die Server-Logs in Render ansehen.")
        finally:
            stop_sampling.set()
            if source is not None and source.exists():
                # Kopie des Originals nach der Verarbeitung entfernen (spart Speicher)
                source.chmod(0o600)
                source.unlink(missing_ok=True)


def start_cleanup_thread(store: JobStore, hours: float, interval_s: float = 600) -> threading.Thread:
    def loop():
        while True:
            try:
                n = store.cleanup(hours)
                if n:
                    log.info("%d alte Jobs gelöscht", n)
            except Exception:
                log.exception("Aufräumen fehlgeschlagen")
            time.sleep(interval_s)

    t = threading.Thread(target=loop, name="autoshorts-cleanup", daemon=True)
    t.start()
    return t
