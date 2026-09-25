import json
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import ergou.db as db_module
from ergou.app import create_app
from ergou.db import Database, Task, now
from ergou.errors import classify_error


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


def test_invalid_tls_option_persists_and_resets_with_replaced_source(client):
    request = {
        "request_id": str(uuid.uuid4()),
        "source": {"url": "https://example.com/video.mp4"},
        "allow_invalid_tls": True,
    }
    task = client.post("/api/v1/tasks", json=request).json()
    assert task["allow_invalid_tls"] is True

    client.app.state.manager.db.update(task["id"], status="failed")
    retried = client.post(f"/api/v1/tasks/{task['id']}/retry", json={}).json()
    assert retried["allow_invalid_tls"] is True

    client.app.state.manager.db.update(task["id"], status="failed")
    replaced = client.post(
        f"/api/v1/tasks/{task['id']}/retry",
        json={"source": {"url": "https://example.net/replacement.mp4"}},
    ).json()
    assert replaced["allow_invalid_tls"] is True

    client.app.state.manager.db.update(task["id"], status="failed")
    sensitive = client.post(
        f"/api/v1/tasks/{task['id']}/retry",
        json={
            "source": {"url": "https://example.net/private.mp4"},
            "context": {
                "cookies": [
                    {
                        "name": "session",
                        "value": "secret",
                        "domain": "example.net",
                        "host_only": True,
                    }
                ]
            },
        },
    ).json()
    assert sensitive["allow_invalid_tls"] is True


def test_invalid_tls_defaults_on_for_every_task(client):
    def create_task(suffix, **values):
        return client.post(
            "/api/v1/tasks",
            json={
                "request_id": str(uuid.uuid4()),
                "source": {"url": f"https://example.com/{suffix}.mp4"},
                **values,
            },
        ).json()

    assert create_task("public")["allow_invalid_tls"] is True
    context = {
        "requests": [
            {"url": "https://example.com/private.mp4", "headers": {"authorization": "Bearer secret"}}
        ]
    }
    assert create_task("private", context=context)["allow_invalid_tls"] is True
    marked = client.post(
        "/api/v1/tasks",
        json={
            "request_id": str(uuid.uuid4()),
            "source": {"url": "https://example.com/marked.mp4", "requires_session": True},
        },
    ).json()
    assert marked["allow_invalid_tls"] is True
    assert create_task("strict", allow_invalid_tls=False)["allow_invalid_tls"] is False
    assert create_task("override", context=context, allow_invalid_tls=True)["allow_invalid_tls"] is True


def test_certificate_errors_have_a_specific_action():
    for error in [
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate",
        "certificate has expired",
        "hostname mismatch",
    ]:
        assert classify_error(error) == "TLS_CERTIFICATE_ERROR"


def test_existing_database_migrates_invalid_tls_option_to_enabled(tmp_path):
    data_dir = tmp_path / "legacy-data"
    data_dir.mkdir()
    engine = create_engine("sqlite:///" + (data_dir / "ergou.sqlite3").as_posix())
    migration = AlembicConfig()
    migration.set_main_option("script_location", str(Path(db_module.__file__).parent / "migrations"))
    with engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.upgrade(migration, "0002")
        connection.exec_driver_sql(
            """
            INSERT INTO download_tasks
              (id, request_id, title, source_json, status, quality, downloaded_bytes,
               target_dir, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "legacy-request",
                "Legacy",
                json.dumps({"url": "https://example.com/video.mp4"}),
                "failed",
                "best",
                0,
                str(tmp_path),
                now(),
                now(),
            ),
        )
    engine.dispose()

    database = Database(data_dir)
    try:
        task = database.view(database.get("legacy-task"))
        assert task.allow_invalid_tls is True
        assert task.tls_certificate_status == "unchecked"
        assert task.playback_status == "pending"
        assert task.playback_identity is None
    finally:
        database.engine.dispose()


def test_delete_terminal_task_states(client, tmp_path):
    for status in ("failed", "canceled", "interrupted"):
        task_id = f"delete-{status}"
        with Session(client.app.state.manager.db.engine) as session:
            session.add(
                Task(
                    id=task_id,
                    request_id=f"delete-request-{status}",
                    title=status,
                    source_json=json.dumps({"url": "https://example.com/video.mp4"}),
                    status=status,
                    quality="best",
                    downloaded_bytes=0,
                    target_dir=str(tmp_path),
                    created_at=now(),
                    updated_at=now(),
                )
            )
            session.commit()
        assert client.delete(f"/api/v1/tasks/{task_id}").json() == {"ok": True}
        assert client.get(f"/api/v1/tasks/{task_id}").status_code == 404


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
        assert response.status_code == 200
        assert response.json()["allow_invalid_tls"] is True


def test_websocket_auth_and_snapshot_ready(client, config):
    with client.websocket_connect("/api/v1/events", headers={"Origin": "http://127.0.0.1:17890"}) as ws:
        ws.send_json({"token": config.token})
        assert ws.receive_json()["type"] == "ready"
