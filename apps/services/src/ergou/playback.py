"""A single playback preparation queue independent of the download scheduler."""

import asyncio
from collections import OrderedDict
import json
from pathlib import Path
import shutil

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Task
from .errors import TaskError, error_info
from .playback_worker import fingerprint
from .process import WorkerProcess

ACTIVE = {"checking", "remuxing", "transcode_queued", "transcoding"}
READY = {"ready_original", "ready_compatible"}


class PlaybackManager:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager.db
        self.config = manager.config
        self.queue = OrderedDict()
        self.progress = {}
        self.encoder = None
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.running_id = None
        self.job = None
        self.stopping = False

    def start(self):
        # A killed service cannot execute worker cleanup; only remove known partials under our own root.
        with Session(self.db.engine) as session:
            for row in session.scalars(select(Task).where(Task.playback_status == "interrupted")):
                if row.playback_fingerprint:
                    partial = (self.root(row.id) / row.playback_fingerprint / "media.part.mp4").resolve()
                    if partial.parent.parent == self.root(row.id):
                        try:
                            partial.unlink(missing_ok=True)
                        except OSError:
                            self.db.update(row.id, playback_error_json=json.dumps(error_info("DISK_ERROR")))
        self.scheduler = asyncio.create_task(self.schedule())

    async def close(self):
        self.stopping = True
        self.scheduler.cancel()
        await asyncio.gather(self.scheduler, return_exceptions=True)
        self.queue.clear()

    def root(self, task_id):
        root = (self.config.data_dir / "playback").resolve()
        directory = (root / task_id).resolve()
        if directory.parent != root:
            raise TaskError("TASK_DELETE_FAILED")
        return directory

    def cleanup(self, task_id):
        directory = self.root(task_id)
        if directory.exists():
            shutil.rmtree(directory)

    def enqueue(self, task_id, mode="recommended"):
        if task_id in self.queue or task_id == self.running_id:
            return
        self.queue[task_id] = mode
        self.db.update(
            task_id,
            playback_status="transcode_queued" if mode == "transcode" else "checking",
            playback_error_json=None,
        )
        self.manager.publish(task_id)
        self.wakeup.set()

    async def prepare(self, task_id, mode):
        async with self.lock:
            source = self.manager.playable_file(task_id)
            row = self.db.get(task_id)
            if task_id in self.queue or task_id == self.running_id:
                return self.manager.get(task_id)
            current = fingerprint(source)
            if row.playback_status in READY and row.playback_fingerprint == current:
                selected = Path(row.playback_path) if row.playback_path else source
                if selected.is_file() and fingerprint(selected) == row.playback_identity:
                    if mode != "transcode" or row.playback_method == "transcode":
                        return self.manager.get(task_id)
            # Retry a previously requested transcode with the same source without requiring a second opt-in.
            if (
                row.playback_method == "transcode"
                and row.playback_status in {"failed", "canceled", "interrupted"}
                and row.playback_fingerprint == current
            ):
                mode = "transcode"
            self.manager.revoke_playback(task_id)
            try:
                self.cleanup(task_id)
            except OSError:
                raise TaskError("TASK_DELETE_FAILED") from None
            self.db.update(
                task_id,
                playback_path=None,
                playback_identity=None,
                playback_fingerprint=current,
                playback_method="transcode" if mode == "transcode" else None,
            )
            self.enqueue(task_id, mode)
            return self.manager.get(task_id)

    async def cancel(self, task_id, publish=True):
        # Callers hold lock, including deletion, so a new prepare cannot overtake cleanup.
        row = self.db.get(task_id)
        if row is None:
            raise KeyError(task_id)
        self.queue.pop(task_id, None)
        self.manager.revoke_playback(task_id)
        if row.playback_status in ACTIVE:
            self.db.update(task_id, playback_status="canceled")
        if self.running_id == task_id and self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
        self.progress.pop(task_id, None)
        if publish:
            self.manager.publish(task_id)
        self.wakeup.set()
        return self.manager.get(task_id)

    async def schedule(self):
        while not self.stopping:
            self.wakeup.clear()
            if self.queue:
                task_id, mode = self.queue.popitem(last=False)
                self.running_id = task_id
                self.job = asyncio.create_task(self.run(task_id, mode))
                try:
                    await self.job
                except asyncio.CancelledError:
                    if self.stopping:
                        raise
                finally:
                    self.running_id = None
                    self.job = None
                continue
            await self.wakeup.wait()

    async def run(self, task_id, mode):
        wrapper = None
        temporary = None
        try:
            source = self.manager.playable_file(task_id)
            identity = fingerprint(source)
            if not self.config.binary("ffmpeg") or not self.config.binary("ffprobe"):
                raise TaskError("DEPENDENCY_MISSING")
            temporary = self.root(task_id) / identity / "media.part.mp4"
            self.db.update(task_id, playback_fingerprint=identity, playback_status="checking")
            self.manager.publish(task_id)
            wrapper = await WorkerProcess.start(
                {
                    "source": str(source),
                    "target": str(temporary),
                    "mode": mode,
                    "ffmpeg": self.config.binary("ffmpeg"),
                    "ffprobe": self.config.binary("ffprobe"),
                    "encoder": self.encoder,
                },
                module="ergou.playback_worker",
            )
            completed = None
            async for line in wrapper.process.stdout:
                event = json.loads(line)
                row = self.db.get(task_id)
                if row is None or row.playback_status not in ACTIVE:
                    continue
                kind = event["type"]
                if kind == "metadata":
                    self.db.update(
                        task_id,
                        playback_media_json=json.dumps(event["media"]),
                        playback_method=event["method"],
                    )
                elif kind == "stage":
                    self.db.update(task_id, playback_status=event["status"])
                elif kind == "progress":
                    self.progress[task_id] = {
                        key: event[key] for key in ("playback_progress", "playback_speed", "playback_eta")
                    }
                elif kind == "encoder":
                    self.encoder = event["encoder"]
                elif kind == "error":
                    raise TaskError(event["error"]["code"])
                elif kind == "complete":
                    completed = event["status"]
                self.manager.publish(task_id)
            if await wrapper.process.wait() or not completed:
                raise TaskError("PLAYBACK_PREPARATION_FAILED")
            if fingerprint(source) != identity:
                raise TaskError("PLAYBACK_SOURCE_CHANGED")
            selected = source
            if completed == "ready_compatible":
                selected = temporary.with_name("media.mp4")
                temporary.replace(selected)
            self.manager.revoke_playback(task_id)
            self.db.update(
                task_id,
                playback_status=completed,
                playback_path=str(selected) if completed == "ready_compatible" else None,
                playback_identity=fingerprint(selected) if completed in READY else None,
                playback_error_json=None,
            )
        except asyncio.CancelledError:
            row = self.db.get(task_id)
            if row and row.playback_status in ACTIVE:
                self.db.update(task_id, playback_status="interrupted")
            raise
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, TaskError)
                else "DISK_ERROR"
                if isinstance(exc, OSError)
                else "PLAYBACK_PREPARATION_FAILED"
            )
            self.db.update(
                task_id, playback_status="failed", playback_error_json=json.dumps(error_info(code))
            )
        finally:
            if wrapper:
                await wrapper.stop()
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    self.db.update(task_id, playback_error_json=json.dumps(error_info("DISK_ERROR")))
            self.progress.pop(task_id, None)
            if self.db.get(task_id):
                self.manager.publish(task_id)
