import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ergou.app import create_app
from ergou.config import Config
from ergou.db import Task, now
from ergou.errors import TaskError
from ergou.manager import Manager
from ergou.process import WorkerProcess
from ergou import playback_worker as worker


def add_media(manager, root, media, task_id="media"):
    path = root / (task_id + media.suffix)
    shutil.copyfile(media, path)
    with Session(manager.db.engine) as session:
        session.add(
            Task(
                id=task_id,
                request_id=f"request-{task_id}",
                title=task_id,
                source_json=json.dumps({"url": "https://example.com/media"}),
                status="completed",
                quality="best",
                output_path=str(path),
                total_bytes=path.stat().st_size,
                target_dir=str(root),
                created_at=now(),
                updated_at=now(),
            )
        )
        session.commit()
    return path


def wait_playback(client, task_id, states, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["playback_status"] in states:
            return task
        assert task["playback_status"] != "failed", task
        time.sleep(0.05)
    pytest.fail(f"playback timed out: {task}")


@pytest.fixture
def media_client(config):
    config.ffmpeg_dir = str(Path(Config.load().binary("ffmpeg")).parent)
    with TestClient(create_app(config)) as client:
        client.headers["Authorization"] = "Bearer " + config.token
        yield client


@pytest.mark.parametrize(
    "name,expected",
    [("sample.mp4", "ready_original"), ("sample.webm", "ready_original"), ("remux.mkv", "ready_compatible")],
)
def test_real_inspection_and_lossless_remux(media_client, media_dir, tmp_path, name, expected):
    client = media_client
    manager = client.app.state.manager
    original = add_media(manager, tmp_path, media_dir / name)
    before = original.read_bytes()
    assert client.post("/api/v1/tasks/media/playback").status_code == 409
    assert client.post("/api/v1/tasks/media/playback/prepare", json={}).status_code == 200
    task = wait_playback(client, "media", {expected})
    assert task["status"] == "completed"
    assert original.read_bytes() == before
    row = manager.db.get("media")
    assert row.playback_media_json
    session = client.post("/api/v1/tasks/media/playback").json()
    assert session["playback_identity"] == task["playback_identity"]
    response = client.get(session["url"], headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    if expected == "ready_original":
        assert row.playback_path is None
        assert not manager.playback.root("media").exists()
    else:
        copy = Path(row.playback_path)
        assert copy.exists() and copy.suffix == ".mp4"
        assert json.loads(row.playback_media_json)["video"]["codec_name"] == "h264"
        assert client.head(session["url"]).headers["content-type"] == "video/mp4"
        assert (
            client.post("/api/v1/tasks/media/playback", json={"source": "original"}).json()[
                "playback_identity"
            ]
            != task["playback_identity"]
        )
    assert client.delete("/api/v1/tasks/media").status_code == 200
    assert original.exists()
    assert not manager.playback.root("media").exists()
    assert client.get(session["url"]).status_code == 401


@pytest.mark.parametrize("name", ["incompatible.mkv", "odd.mkv", "hdr.mkv"])
def test_real_transcode_requires_opt_in_and_handles_special_media(media_client, media_dir, tmp_path, name):
    client = media_client
    manager = client.app.state.manager
    manager.playback.encoder = "libx264"  # deterministic real CPU coverage, hardware paths tested separately
    original = add_media(manager, tmp_path, media_dir / name)
    original_hash = worker.fingerprint(original)
    client.post("/api/v1/tasks/media/playback/prepare", json={})
    wait_playback(client, "media", {"transcode_required"})
    assert not manager.playback.root("media").exists()
    old = client.post("/api/v1/tasks/media/playback", json={"source": "original"}).json()
    assert client.post("/api/v1/tasks/media/playback/prepare", json={"mode": "transcode"}).status_code == 200
    task = wait_playback(client, "media", {"ready_compatible"})
    assert task["playback_method"] == "transcode"
    result = worker.probe(manager.config.binary("ffprobe"), manager.db.get("media").playback_path)
    assert result["video"]["codec_name"] == "h264"
    assert result["video"]["pix_fmt"] == "yuv420p"
    assert result["video"]["width"] % 2 == 0 and result["video"]["height"] % 2 == 0
    if name == "hdr.mkv":
        assert result["video"]["color_transfer"] == "bt709"
    if name != "incompatible.mkv":
        assert result["audio"] is None
    else:
        assert result["audio"]["codec_name"] == "aac"
    assert worker.fingerprint(original) == original_hash
    assert client.get(old["url"]).status_code == 401
    assert not list(manager.playback.root("media").rglob("*.part.mp4"))
    assert client.post("/api/v1/tasks/media/playback").status_code == 200
    # A file replacement must never be served through a session authorized for the previous file.
    original.write_bytes(b"replaced")
    assert client.get(old["url"]).json()["detail"]["code"] == "PLAYBACK_SOURCE_CHANGED"


@pytest.mark.parametrize("available", ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"])
def test_encoder_probe_uses_actual_encoding_and_falls_back(monkeypatch, available):
    calls = []

    def run(cmd, **kwargs):
        encoder = cmd[cmd.index("-c:v") + 1]
        calls.append(encoder)
        assert "-frames:v" in cmd and kwargs["timeout"] == 10
        if encoder != available:
            raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(worker.subprocess, "run", run)
    assert worker.choose_encoder("ffmpeg") == available
    assert calls == list(worker.ENCODERS[: len(calls)])


@pytest.mark.parametrize("encoder", worker.ENCODERS)
def test_failed_hardware_conversion_restarts_with_cpu(monkeypatch, tmp_path, encoder):
    target = tmp_path / "media.part.mp4"
    events, calls = [], []
    media = {
        "container": "mov,mp4",
        "duration": 1,
        "video": {"codec_name": "h264", "pix_fmt": "yuv420p"},
        "audio": None,
    }
    monkeypatch.setattr(worker, "probe", lambda *_: media)
    monkeypatch.setattr(worker, "emit", lambda kind, **data: events.append((kind, data)))

    def convert(_, source, path, *args):
        selected = args[-1]
        calls.append(selected)
        if selected == encoder:
            path.write_bytes(b"broken")
            raise TaskError("PLAYBACK_PREPARATION_FAILED")
        assert not path.exists()
        path.write_bytes(b"good")

    monkeypatch.setattr(worker, "convert", convert)
    worker.prepare(
        {
            "source": "source.mkv",
            "target": str(target),
            "mode": "transcode",
            "encoder": encoder,
            "ffmpeg": "ffmpeg",
            "ffprobe": "ffprobe",
        }
    )
    assert calls == [encoder, "libx264"]
    assert events[-1] == ("complete", {"status": "ready_compatible"})


def test_unknown_duration_and_progress_values():
    assert worker.progress_event({"out_time_us": "2500000", "speed": "2.5x"}, 10) == {
        "playback_progress": 25,
        "playback_speed": 2.5,
        "playback_eta": 3,
    }
    assert worker.progress_event({"out_time_us": "N/A", "speed": "N/A"}, None) == {
        "playback_progress": None,
        "playback_speed": None,
        "playback_eta": None,
    }


async def test_fifo_cancel_queue_and_restart_preserve_completed_download(
    config, media_dir, tmp_path, monkeypatch
):
    manager = Manager(config)
    order, release = [], asyncio.Event()

    async def blocked(task_id, mode):
        order.append(task_id)
        await release.wait()

    monkeypatch.setattr(manager.playback, "run", blocked)
    await manager.start()
    try:
        for name in ("one", "two", "three"):
            add_media(manager, tmp_path, media_dir / "sample.mp4", name)
            await manager.playback.prepare(name, "recommended")
        await asyncio.sleep(0.03)
        assert order == ["one"]
        assert manager.running == {}  # no download slots consumed
        async with manager.playback.lock:
            await manager.playback.cancel("two")
        release.set()
        await asyncio.sleep(0.05)
        assert order == ["one", "three"]
        assert manager.get("two").playback_status == "canceled"
    finally:
        await manager.close()
    restarted = Manager(config)
    await restarted.start()
    try:
        assert restarted.get("one").status == "completed"
        assert restarted.get("one").playback_status == "interrupted"
        assert restarted.playback.queue == {}
        assert restarted.get("two").playback_status == "canceled"
    finally:
        await restarted.close()


@pytest.mark.parametrize("operation", ["cancel", "delete"])
def test_cancel_and_delete_kill_real_ffmpeg_tree_and_release_queue(
    media_client, media_dir, tmp_path, monkeypatch, operation
):
    import os

    client = media_client
    manager = client.app.state.manager
    manager.playback.encoder = "libx264"
    long_media = tmp_path / "long.mkv"
    subprocess.run(
        [
            manager.config.binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-stream_loop",
            "99",
            "-i",
            str(media_dir / "sample.mp4"),
            "-c",
            "copy",
            str(long_media),
        ],
        check=True,
        timeout=30,
    )
    original = add_media(manager, tmp_path, long_media)
    add_media(manager, tmp_path, media_dir / "sample.mp4", "next")
    wrappers = []
    start = WorkerProcess.start

    async def capture(payload, module="ergou.worker"):
        wrapper = await start(payload, module)
        if module == "ergou.playback_worker":
            wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(WorkerProcess, "start", capture)
    client.post("/api/v1/tasks/media/playback/prepare", json={"mode": "transcode"}).raise_for_status()
    wait_playback(client, "media", {"transcoding"})
    deadline = time.monotonic() + 10
    while not list(manager.playback.root("media").rglob("*.part.mp4")):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    wrapper = wrappers[0]
    job_handle = None
    if os.name == "nt":
        import win32api
        import win32con
        import win32job

        process = win32api.GetCurrentProcess()
        job_handle = win32api.DuplicateHandle(
            process, wrapper.job, process, 0, False, win32con.DUPLICATE_SAME_ACCESS
        )
        assert (
            win32job.QueryInformationJobObject(job_handle, win32job.JobObjectBasicAccountingInformation)[
                "ActiveProcesses"
            ]
            >= 2
        )
    try:
        client.post("/api/v1/tasks/next/playback/prepare", json={}).raise_for_status()
        if operation == "cancel":
            client.post("/api/v1/tasks/media/playback/cancel").raise_for_status()
            assert client.get("/api/v1/tasks/media").json()["playback_status"] == "canceled"
        else:
            client.delete("/api/v1/tasks/media").raise_for_status()
            assert client.get("/api/v1/tasks/media").status_code == 404
        assert wrapper.process.returncode is not None
        if job_handle:
            while win32job.QueryInformationJobObject(
                job_handle, win32job.JobObjectBasicAccountingInformation
            )["ActiveProcesses"]:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        assert original.exists()
        assert not list(manager.playback.root("media").rglob("*.part.mp4"))
        assert wait_playback(client, "next", {"ready_original"})["status"] == "completed"
    finally:
        if job_handle:
            job_handle.Close()


def test_failed_inspection_can_retry_and_missing_copy_can_regenerate(media_client, media_dir, tmp_path):
    client = media_client
    manager = client.app.state.manager
    source = add_media(manager, tmp_path, media_dir / "remux.mkv")
    source.write_bytes(b"broken")
    client.post("/api/v1/tasks/media/playback/prepare", json={}).raise_for_status()
    task = wait_playback(client, "media", {"failed"})
    assert task["status"] == "completed" and task["playback_error"]["code"] == "PLAYBACK_PREPARATION_FAILED"
    shutil.copyfile(media_dir / "remux.mkv", source)
    client.post("/api/v1/tasks/media/playback/prepare", json={}).raise_for_status()
    wait_playback(client, "media", {"ready_compatible"})
    Path(manager.db.get("media").playback_path).unlink()
    assert client.post("/api/v1/tasks/media/playback").status_code == 404
    client.post("/api/v1/tasks/media/playback/prepare", json={}).raise_for_status()
    wait_playback(client, "media", {"ready_compatible"})
    assert client.post("/api/v1/tasks/media/playback").status_code == 200
    client.delete("/api/v1/tasks/media?delete_file=true").raise_for_status()
    assert not source.exists()


def test_prepare_and_cancel_require_authentication_and_completed_file(client, tmp_path):
    assert (
        client.post(
            "/api/v1/tasks/missing/playback/prepare", json={}, headers={"Authorization": ""}
        ).status_code
        == 401
    )
    assert (
        client.post("/api/v1/tasks/missing/playback/cancel", headers={"Authorization": ""}).status_code == 401
    )
    assert client.post("/api/v1/tasks/missing/playback/prepare", json={}).status_code == 404
