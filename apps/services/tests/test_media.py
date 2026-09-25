import json
import time
import uuid
from pathlib import Path
import subprocess

import pytest

from ergou.config import Config
from ergou.worker import ScopedYoutubeDL, add_cookies, prepare_resume
from conftest import wait_task


def create(client, url, **kwargs):
    response = client.post(
        "/api/v1/tasks", json={"request_id": str(uuid.uuid4()), "source": {"url": url}, **kwargs}
    )
    response.raise_for_status()
    return response.json()["id"]


def resolve(client, url, **kwargs):
    result = client.post("/api/v1/resolutions", json={"source": {"url": url}, **kwargs}).json()
    for _ in range(200):
        result = client.get("/api/v1/resolutions/" + result["id"]).json()
        if result["status"] != "resolving":
            return result
        time.sleep(0.1)
    raise AssertionError(f"Resolution timed out: {result}")


@pytest.mark.parametrize(
    "route", ["sample.mp4", "sample.webm", "hls/index.m3u8", "master.m3u8", "dash/index.mpd", "stream"]
)
def test_download_media_with_audio(media_client, media_server, route):
    task = wait_task(media_client, create(media_client, f"{media_server[0]}/{route}"))
    assert task["status"] == "completed", task
    path = Path(task["output_path"])
    assert path.is_file()
    result = subprocess.run(
        [
            Config.load().binary("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    probe = json.loads(result.stdout)
    assert {"video", "audio"} <= {s["codec_type"] for s in probe["streams"]}
    assert 5.8 < float(probe["format"]["duration"]) < 6.5


def test_resolution_returns_quality_choices(media_client, media_server):
    r = media_client.post(
        "/api/v1/resolutions", json={"source": {"url": media_server[0] + "/multi.m3u8"}}
    ).json()
    for _ in range(200):
        r = media_client.get("/api/v1/resolutions/" + r["id"]).json()
        if r["status"] != "resolving":
            break
        time.sleep(0.1)
    assert r["status"] == "completed", r
    assert r["media"]["formats"]


@pytest.mark.parametrize(
    "route,code",
    [
        ("encrypted.m3u8", "DRM_UNSUPPORTED"),
        ("live.m3u8", "LIVE_UNSUPPORTED"),
        ("gone.mp4", "SOURCE_EXPIRED"),
        ("auth/sample.mp4", "AUTH_REQUIRED"),
    ],
)
def test_media_failure_is_actionable(media_client, media_server, route, code):
    task = wait_task(media_client, create(media_client, media_server[0] + "/" + route))
    assert task["status"] == "failed", task
    assert task["error"]["code"] == code, task


def test_invalid_https_certificate_requires_explicit_task_option(media_client, invalid_https_media_server):
    strict_resolution = resolve(
        media_client,
        invalid_https_media_server + "/sample.mp4",
        allow_invalid_tls=False,
    )
    compatible_resolution = resolve(media_client, invalid_https_media_server + "/sample.mp4")
    assert strict_resolution["status"] == "failed", strict_resolution
    assert strict_resolution["error"]["code"] == "TLS_CERTIFICATE_ERROR", strict_resolution
    assert compatible_resolution["status"] == "completed", compatible_resolution

    strict_id = create(
        media_client,
        invalid_https_media_server + "/sample.mp4",
        allow_invalid_tls=False,
    )
    compatible_id = create(media_client, invalid_https_media_server + "/sample.mp4")
    strict = wait_task(media_client, strict_id)
    compatible = wait_task(media_client, compatible_id)
    assert strict["status"] == "failed", strict
    assert strict["error"]["code"] == "TLS_CERTIFICATE_ERROR", strict
    assert strict["allow_invalid_tls"] is False
    assert compatible["status"] == "completed", compatible
    assert compatible["allow_invalid_tls"] is True


def test_sensitive_https_task_uses_compatibility_by_default(
    media_client, invalid_https_media_server
):
    url = invalid_https_media_server + "/sample.mp4"
    context = {
        "cookies": [{"name": "session", "value": "valid", "domain": "127.0.0.1", "host_only": True}]
    }
    compatible = wait_task(media_client, create(media_client, url, context=context))
    assert compatible["status"] == "completed", compatible
    assert compatible["allow_invalid_tls"] is True
    deadline = time.monotonic() + 10
    while compatible["tls_certificate_status"] == "checking" and time.monotonic() < deadline:
        time.sleep(0.05)
        compatible = media_client.get(f"/api/v1/tasks/{compatible['id']}").json()
    assert compatible["tls_certificate_status"] == "invalid"


@pytest.mark.parametrize("route", ["master.m3u8", "dash/index.mpd"])
def test_invalid_tls_option_covers_manifests_fragments_and_tracks(
    media_client, invalid_https_media_server, route
):
    task = wait_task(
        media_client,
        create(media_client, f"{invalid_https_media_server}/{route}", allow_invalid_tls=True),
    )
    assert task["status"] == "completed", task
    probe = subprocess.run(
        [
            Config.load().binary("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            task["output_path"],
        ],
        capture_output=True,
        check=True,
    )
    stream_types = {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]}
    assert {"video", "audio"} <= stream_types


def test_missing_fragment_cannot_complete(media_client, media_server):
    task = wait_task(media_client, create(media_client, media_server[0] + "/missing/index.m3u8"))
    assert task["status"] == "failed", task
    assert task["output_path"] is None


def test_scoped_login_context_downloads_without_persisting_secrets(media_client, media_server, config):
    url = media_server[0] + "/auth/sample.mp4"
    context = {
        "cookies": [{"name": "session", "value": "valid", "domain": "127.0.0.1", "host_only": True}],
        "requests": [{"url": url, "headers": {"referer": "https://page.test/watch"}}],
    }
    task = wait_task(media_client, create(media_client, url, context=context))
    assert task["status"] == "completed", task
    assert "cookies" not in json.dumps(task)
    assert b'"value": "valid"' not in (config.data_dir / "ergou.sqlite3").read_bytes()


def test_cancel_stops_worker_and_retry_reuses_partial(media_client, media_server):
    request_start = len(media_server[1])
    task_id = create(media_client, media_server[0] + "/slow.mp4")
    wait_task(media_client, task_id, {"downloading"})
    time.sleep(0.4)
    canceled = media_client.post(f"/api/v1/tasks/{task_id}/cancel").json()
    assert canceled["status"] == "canceled"
    assert task_id not in media_client.app.state.manager.running
    response = media_client.post(f"/api/v1/tasks/{task_id}/retry", json={})
    assert response.status_code == 200
    task = wait_task(media_client, task_id)
    assert task["status"] == "completed", task
    assert any(
        int(r["headers"].get("Range", "bytes=0-").split("=")[1].split("-")[0]) > 0
        for r in media_server[1][request_start:]
        if r["path"] == "/slow.mp4"
    )


def test_queue_limit_and_cancel_release_slot(media_client, media_server):
    settings = media_client.get("/api/v1/settings").json()
    settings["concurrency"] = 1
    media_client.patch("/api/v1/settings", json=settings).raise_for_status()
    first = create(media_client, media_server[0] + "/slow.mp4")
    wait_task(media_client, first, {"downloading"})
    second = create(media_client, media_server[0] + "/sample.mp4")
    time.sleep(0.2)
    assert media_client.get(f"/api/v1/tasks/{second}").json()["status"] == "queued"
    media_client.post(f"/api/v1/tasks/{first}/cancel").raise_for_status()
    assert wait_task(media_client, second)["status"] == "completed"
    assert media_client.get(f"/api/v1/tasks/{first}").json()["status"] == "canceled"


def test_concurrent_same_titles_do_not_overwrite_files(media_client, media_server):
    ids = [create(media_client, media_server[0] + "/sample.mp4") for _ in range(2)]
    tasks = [wait_task(media_client, task_id) for task_id in ids]
    assert all(task["status"] == "completed" for task in tasks), tasks
    files = [Path(task["output_path"]) for task in tasks]
    assert files[0] != files[1]
    assert files[0].read_bytes() == files[1].read_bytes()


def test_delete_task_keeps_or_removes_completed_file(media_client, media_server, config):
    kept_id = create(media_client, media_server[0] + "/sample.mp4")
    kept = wait_task(media_client, kept_id)
    kept_path = Path(kept["output_path"])
    assert media_client.delete(f"/api/v1/tasks/{kept_id}").json() == {"ok": True}
    assert media_client.get(f"/api/v1/tasks/{kept_id}").status_code == 404
    assert kept_path.is_file()
    assert not (config.data_dir / "work" / kept_id).exists()

    removed_id = create(media_client, media_server[0] + "/sample.mp4")
    removed = wait_task(media_client, removed_id)
    removed_path = Path(removed["output_path"])
    assert media_client.delete(f"/api/v1/tasks/{removed_id}?delete_file=true").json() == {"ok": True}
    assert media_client.get(f"/api/v1/tasks/{removed_id}").status_code == 404
    assert not removed_path.exists()

    missing_id = create(media_client, media_server[0] + "/sample.mp4")
    missing = wait_task(media_client, missing_id)
    Path(missing["output_path"]).unlink()
    assert media_client.delete(f"/api/v1/tasks/{missing_id}?delete_file=true").json() == {"ok": True}
    assert media_client.get(f"/api/v1/tasks/{missing_id}").status_code == 404


def test_delete_active_task_stops_worker_and_releases_queue(media_client, media_server, config):
    settings = media_client.get("/api/v1/settings").json()
    settings["concurrency"] = 1
    media_client.patch("/api/v1/settings", json=settings).raise_for_status()
    active_id = create(media_client, media_server[0] + "/slow.mp4")
    wait_task(media_client, active_id, {"resolving", "downloading"})
    queued_id = create(media_client, media_server[0] + "/sample.mp4")

    assert media_client.delete(f"/api/v1/tasks/{active_id}").json() == {"ok": True}
    assert active_id not in media_client.app.state.manager.running
    assert media_client.get(f"/api/v1/tasks/{active_id}").status_code == 404
    assert not (config.data_dir / "work" / active_id).exists()
    assert wait_task(media_client, queued_id)["status"] == "completed"


def test_failed_output_deletion_preserves_task(media_client, media_server, monkeypatch):
    task_id = create(media_client, media_server[0] + "/sample.mp4")
    task = wait_task(media_client, task_id)
    output = Path(task["output_path"])
    original_unlink = Path.unlink

    def fail_output(self, *args, **kwargs):
        if self == output:
            raise PermissionError("in use")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_output)
    response = media_client.delete(f"/api/v1/tasks/{task_id}?delete_file=true")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_DELETE_FAILED"
    assert media_client.get(f"/api/v1/tasks/{task_id}").status_code == 200
    assert output.exists()


def test_authorization_not_forwarded_cross_origin(media_server):
    url = media_server[0] + "/redirect"
    context = {"requests": [{"url": url, "headers": {"authorization": "Bearer test-secret"}}]}
    with ScopedYoutubeDL({"quiet": True}, context) as ydl:
        add_cookies(ydl, context, {"url": url})
        with ydl.urlopen(url) as response:
            response.read(1)
    redirect_request = next(r for r in reversed(media_server[1]) if r["path"] == "/redirect")
    target_request = next(r for r in reversed(media_server[1]) if r["path"] == "/sample.mp4")
    assert redirect_request["headers"].get("Authorization") == "Bearer test-secret"
    assert "Authorization" not in target_request["headers"]


def test_resume_discards_partial_when_media_identity_changes(media_server, tmp_path):
    with ScopedYoutubeDL({"quiet": True}, {}) as ydl:
        info = {"url": media_server[0] + "/stream", "protocol": "http"}
        prepare_resume(ydl, info, tmp_path)
        partial = tmp_path / "media.mp4.part"
        partial.write_bytes(b"partial")
        prepare_resume(ydl, info, tmp_path)
        assert partial.exists()
        (tmp_path / "identity").write_text("a different upstream video")
        prepare_resume(ydl, info, tmp_path)
        assert not partial.exists()
