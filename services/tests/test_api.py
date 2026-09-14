import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ergou.app import create_app
from ergou.db import Task, now


def test_authentication_and_origins(client):
    assert client.get("/api/v1/health", headers={"Authorization": ""}).status_code == 200
    assert client.get("/api/v1/tasks", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/v1/tasks", headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get("/api/v1/tasks", headers={"Host": "evil.test"}).status_code == 400
    assert (
        client.get("/api/v1/tasks", headers={"Origin": "chrome-extension://" + "a" * 32}).status_code == 200
    )


def test_idempotent_creation_and_group_filter(client):
    request = {"request_id": str(uuid.uuid4()), "source": {"url": "https://example.com/video.mp4"}}
    first = client.post("/api/v1/tasks", json=request)
    second = client.post("/api/v1/tasks", json=request)
    assert first.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/api/v1/tasks").json()["total"] == 1
    # Active and history form a partition of all tasks even if a missing dependency already failed the task.
    assert (
        sum(
            client.get("/api/v1/tasks", params={"group": group}).json()["total"]
            for group in ("active", "history")
        )
        == 1
    )


def test_source_and_path_validation(client):
    for url in ["file:///C:/Windows/win.ini", "blob:https://example.com/id", "http://u:p@example.com/v"]:
        assert (
            client.post(
                "/api/v1/tasks", json={"request_id": str(uuid.uuid4()), "source": {"url": url}}
            ).status_code
            == 422
        )
    assert (
        client.patch("/api/v1/settings", json={"download_dir": "relative/path", "concurrency": 2}).status_code
        == 422
    )
    assert (
        client.patch("/api/v1/settings", json={"download_dir": "C:/videos", "concurrency": 20}).status_code
        == 422
    )


def test_settings_and_interrupted_tasks_survive_restart(config, tmp_path):
    app = create_app(config)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + config.token
        client.patch(
            "/api/v1/settings",
            json={"download_dir": str(tmp_path / "files"), "quality": "720", "concurrency": 1},
        )
        with Session(app.state.manager.db.engine) as session:
            session.add(
                Task(
                    id="interrupted",
                    request_id="recover-test",
                    title="Recover",
                    source_json=json.dumps({"url": "https://example.com/v.mp4", "requires_session": True}),
                    status="downloading",
                    quality="best",
                    downloaded_bytes=123,
                    target_dir=str(tmp_path),
                    created_at=now(),
                    updated_at=now(),
                )
            )
            session.commit()
    with TestClient(create_app(config)) as client:
        client.headers["Authorization"] = "Bearer " + config.token
        assert client.get("/api/v1/settings").json()["quality"] == "720"
        task = client.get("/api/v1/tasks/interrupted").json()
        assert task["status"] == "interrupted"
        assert task["downloaded_bytes"] == 123
        response = client.post("/api/v1/tasks/interrupted/retry", json={})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SESSION_REQUIRED"


def test_websocket_auth_and_snapshot_ready(client, config):
    with client.websocket_connect("/api/v1/events", headers={"Origin": "http://127.0.0.1:17890"}) as ws:
        ws.send_json({"token": config.token})
        assert ws.receive_json()["type"] == "ready"
