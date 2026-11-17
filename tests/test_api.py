"""Tests für Login, Upload-Grenzen, Zugriffsschutz und Download."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_video


def fake_processor(job_dir, source, options, settings, progress, tracker):
    progress(50, "halb fertig")
    out = job_dir / "output"
    out.mkdir(exist_ok=True)
    (out / "short_1.mp4").write_bytes(b"\x00" * 2048)
    (out / "short_1.jpg").write_bytes(b"jpg")
    (out / "shorts.zip").write_bytes(b"PK")
    return {"clips": [{"index": 1, "file": "short_1.mp4", "thumb": "short_1.jpg", "title": "t", "duration": 20}],
            "zip": "shorts.zip", "notes": []}


@pytest.fixture
def client(settings):
    app = create_app(settings, processor=fake_processor, start_worker=False)
    with TestClient(app) as c:
        c.app_ref = app
        yield c


def login(c, pw="test-passwort"):
    return c.post("/api/login", json={"password": pw})


@pytest.fixture(scope="module")
def small_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("v") / "clip.mp4", seconds=3, size="320x240").read_bytes()


def test_everything_requires_login(client):
    for path in ["/api/me", "/api/jobs", "/api/jobs/" + "a" * 32, "/api/jobs/" + "a" * 32 + "/files/shorts.zip"]:
        assert client.get(path).status_code == 401
    assert client.post("/api/upload?filename=a.mp4", content=b"x").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200  # Seite selbst (ohne Daten) ist sichtbar


def test_wrong_password_and_rate_limit(client):
    assert login(client, "falsch").status_code == 401
    for _ in range(10):
        login(client, "falsch")
    r = login(client)  # auch richtiges Passwort ist nach 10 Fehlversuchen gesperrt
    assert r.status_code == 429


def test_login_sets_httponly_cookie(client):
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = client.get("/api/me").json()
    assert me["max_upload_mb"] == 500 and me["transcription"] is False


def test_tampered_cookie_rejected(client):
    client.cookies.set("as_session", "abc.def.ghi")
    assert client.get("/api/me").status_code == 401


def test_no_password_configured(settings):
    settings.app_password = ""
    with TestClient(create_app(settings, start_worker=False)) as c:
        r = c.post("/api/login", json={"password": ""})
        assert r.status_code == 503 and "APP_PASSWORD" in r.json()["detail"]


def test_upload_rejects_wrong_type_and_size(client, settings):
    login(client)
    r = client.post("/api/upload?filename=a.avi", content=b"123")
    assert r.status_code == 400 and "MP4" in r.json()["detail"]
    settings.max_upload_mb = 1
    r = client.post("/api/upload?filename=a.mp4", content=b"0" * (1024 * 1024 + 10))
    assert r.status_code == 413
    assert list(settings.jobs_dir.iterdir()) == []  # nichts liegen geblieben


def test_upload_rejects_non_video(client, settings):
    login(client)
    r = client.post("/api/upload?filename=a.mp4", content=b"das ist kein video")
    assert r.status_code == 400
    assert "Video" in r.json()["detail"]
    assert list(settings.jobs_dir.iterdir()) == []


def test_upload_process_and_download(client, small_video):
    login(client)
    r = client.post("/api/upload?filename=urlaub.mov&clips=9&music=../../etc/passwd", content=small_video)
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "queued"
    assert job["options"]["clips"] == 5  # auf Maximum begrenzt

    store = client.app_ref.state.store
    original = store.dir(job_id) / "original.mov"
    assert original.read_bytes() == small_video
    client.app_ref.state.worker.run_one(job_id)

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["progress"] == 100
    assert not original.exists()  # Server-Kopie aufgeräumt

    r = client.get(f"/api/jobs/{job_id}/files/short_1.mp4")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    r = client.get(f"/api/jobs/{job_id}/files/short_1.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100  # wichtig für iPhone-Vorschau
    r = client.get(f"/api/jobs/{job_id}/files/shorts.zip")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    # Pfad-Tricks und fremde Dateien sind gesperrt
    assert client.get(f"/api/jobs/{job_id}/files/job.json").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/files/..%2Fjob.json").status_code == 404
    assert client.get("/api/jobs/../../etc/passwd").status_code == 404

    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_failed_job_shows_friendly_error(settings, small_video):
    def boom(*a, **k):
        raise RuntimeError("interner Stacktrace mit /geheimen/pfaden")
    app = create_app(settings, processor=boom, start_worker=False)
    with TestClient(app) as c:
        login(c)
        job_id = c.post("/api/upload?filename=a.mp4", content=small_video).json()["id"]
        app.state.worker.run_one(job_id)
        job = c.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "failed"
        assert "geheimen" not in job["error"] and "technischer Fehler" in job["error"]


def test_cleanup_removes_old_jobs(client, small_video):
    login(client)
    job_id = client.post("/api/upload?filename=a.mp4", content=small_video).json()["id"]
    store = client.app_ref.state.store
    store.update(job_id, status="done")
    assert store.cleanup(max_age_hours=1) == 0
    job = store.get(job_id)
    job["updated"] = time.time() - 7200
    (store.dir(job_id) / "job.json").write_text(__import__("json").dumps(job))
    assert store.cleanup(max_age_hours=1) == 1
    assert not store.dir(job_id).exists()


def test_restart_marks_running_jobs_failed(settings):
    app = create_app(settings, start_worker=False)
    store = app.state.store
    store.create(store.new_id(), "x.mp4", {})
    with TestClient(app) as c:
        login(c)
        jobs = c.get("/api/jobs").json()
        assert jobs[0]["status"] == "failed" and "neu gestartet" in jobs[0]["error"]


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_diag_endpoint_and_interrupted_message(settings):
    app = create_app(settings, start_worker=False)
    store = app.state.store
    jid = store.new_id()
    store.create(jid, "x.mp4", {})
    store.update(jid, status="running", progress=53.3, message="Short 1 von 3: Rendern", diag="Speicher 400/512 MB")
    with TestClient(app) as c:
        login(c)
        d = c.get("/api/diag").json()
        assert "disk" in d and "summary" in d
        job = c.get(f"/api/jobs/{jid}").json()
        assert "53.3 %" in job["error"] and "Rendern" in job["error"]
        assert job["diag"] == "Speicher 400/512 MB"
