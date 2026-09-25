import json
import time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ergou.app import create_app
from ergou.db import Task, now


def add_task(client, tmp_path, task_id="playable", status="completed", suffix=".mp4", exists=True):
    path = tmp_path / f"{task_id}{suffix}"
    content = bytes(range(256)) * 8
    if exists:
        path.write_bytes(content)
    with Session(client.app.state.manager.db.engine) as session:
        session.add(
            Task(
                id=task_id,
                request_id=f"request-{task_id}",
                title="Local playback",
                source_json=json.dumps({"url": "https://example.com/video.mp4"}),
                status=status,
                quality="best",
                downloaded_bytes=len(content),
                total_bytes=len(content),
                output_path=str(path) if status == "completed" else None,
                target_dir=str(tmp_path),
                created_at=now(),
                updated_at=now(),
            )
        )
        session.commit()
    return path, content


def test_playback_session_streams_ranges_with_scoped_cookie(client, tmp_path):
    path, content = add_task(client, tmp_path)
    unauthorized = client.post(
        "/api/v1/tasks/playable/playback", headers={"Authorization": ""}
    )
    assert unauthorized.status_code == 401

    response = client.post("/api/v1/tasks/playable/playback")
    assert response.status_code == 200
    assert response.json()["url"] == "/api/v1/playback/playable"
    assert response.json()["expires_at"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/api/v1/playback/playable" in cookie

    complete = client.get(response.json()["url"])
    assert complete.status_code == 200
    assert complete.content == content
    assert complete.headers["content-type"] == "video/mp4"
    assert complete.headers["accept-ranges"] == "bytes"
    assert complete.headers["content-disposition"].startswith("inline;")

    partial = client.get(response.json()["url"], headers={"Range": "bytes=100-199"})
    assert partial.status_code == 206
    assert partial.content == content[100:200]
    assert partial.headers["content-range"] == f"bytes 100-199/{len(content)}"
    assert partial.headers["content-length"] == "100"
    assert client.head(response.json()["url"]).headers["content-length"] == str(path.stat().st_size)
    assert client.get(response.json()["url"], headers={"Range": "bytes=99999-"}).status_code == 416


def test_playback_webm_mime_and_session_expiration(client, tmp_path):
    add_task(client, tmp_path, task_id="webm", suffix=".webm")
    media_url = client.post("/api/v1/tasks/webm/playback").json()["url"]
    assert client.head(media_url).headers["content-type"] == "video/webm"

    token = next(iter(client.app.state.manager.playback_sessions))
    client.app.state.manager.playback_sessions[token]["expires_at"] = time.time() - 1
    expired = client.get(media_url)
    assert expired.status_code == 401
    assert expired.json()["detail"]["code"] == "PLAYBACK_UNAUTHORIZED"


def test_playback_rejects_unfinished_missing_and_deleted_tasks(client, tmp_path):
    add_task(client, tmp_path, task_id="active", status="downloading")
    active = client.post("/api/v1/tasks/active/playback")
    assert active.status_code == 409
    assert active.json()["detail"]["code"] == "PLAYBACK_NOT_READY"

    add_task(client, tmp_path, task_id="missing", exists=False)
    missing = client.post("/api/v1/tasks/missing/playback")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PLAYBACK_FILE_MISSING"

    path, _ = add_task(client, tmp_path, task_id="deleted")
    media_url = client.post("/api/v1/tasks/deleted/playback").json()["url"]
    assert client.delete("/api/v1/tasks/deleted").status_code == 200
    assert path.exists()
    assert client.get(media_url).status_code == 401


def test_playback_sessions_do_not_survive_service_restart(config, tmp_path):
    with TestClient(create_app(config)) as first:
        first.headers["Authorization"] = "Bearer " + config.token
        add_task(first, tmp_path, task_id="restart")
        media_url = first.post("/api/v1/tasks/restart/playback").json()["url"]
        cookie = first.cookies.get("ergou_playback")

    with TestClient(create_app(config)) as second:
        second.headers["Authorization"] = "Bearer " + config.token
        second.cookies.set("ergou_playback", cookie, path=media_url)
        response = second.get(media_url)
        assert response.status_code == 401
        renewed = second.post("/api/v1/tasks/restart/playback")
        assert renewed.status_code == 200
        assert second.get(media_url).status_code == 200
