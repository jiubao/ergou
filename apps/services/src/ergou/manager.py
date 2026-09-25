import asyncio
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from yt_dlp.utils import sanitize_filename

from .db import Database, Task, now
from .errors import error_info
from .process import WorkerProcess
from .schemas import ErrorInfo, Resolution, ResolvedMedia
from .tls import certificate_status

log = logging.getLogger("ergou")
TERMINAL = {"completed", "failed", "canceled", "interrupted"}


def effective_invalid_tls(value):
    return value if value is not None else True


class TaskError(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status


class Manager:
    def __init__(self, config):
        self.config = config
        self.db = Database(config.data_dir)
        self.contexts = {}
        self.progress = {}
        self.running = {}
        self.resolutions = {}
        self.resolution_jobs = set()
        self.tls_jobs = {}
        self.resolution_slots = asyncio.Semaphore(2)
        self.subscribers = set()
        self.stopping = False
        self.wakeup = asyncio.Event()

    async def start(self):
        self.db.recover()
        self.scheduler = asyncio.create_task(self.schedule())

    async def close(self):
        self.stopping = True
        self.scheduler.cancel()
        await asyncio.gather(self.scheduler, return_exceptions=True)
        jobs = list(self.running.values()) + list(self.resolution_jobs) + list(self.tls_jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.db.recover()
        self.contexts.clear()
        self.db.engine.dispose()

    def start_tls_diagnostic(self, task_id, url):
        if previous := self.tls_jobs.pop(task_id, None):
            previous.cancel()
        initial = "checking" if url.lower().startswith("https://") else "not_applicable"
        self.db.update(task_id, tls_certificate_status=initial)
        if initial == "not_applicable":
            return
        job = asyncio.create_task(self.run_tls_diagnostic(task_id, url))
        self.tls_jobs[task_id] = job

        def finished(done):
            if self.tls_jobs.get(task_id) is done:
                self.tls_jobs.pop(task_id, None)

        job.add_done_callback(finished)

    async def run_tls_diagnostic(self, task_id, url):
        try:
            status = await certificate_status(url)
            if self.db.get(task_id) is not None:
                self.db.update(task_id, tls_certificate_status=status)
                self.publish(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("task=%s TLS diagnostic failed", task_id)
            if self.db.get(task_id) is not None:
                self.db.update(task_id, tls_certificate_status="unavailable")
                self.publish(task_id)

    def get(self, task_id):
        row = self.db.get(task_id)
        if row is None:
            raise KeyError(task_id)
        return self.db.view(row, self.progress.get(task_id))

    def listing(self, offset=0, limit=50, status=None, search=None, group=None):
        with Session(self.db.engine) as session:
            query = select(Task)
            if status:
                query = query.where(Task.status == status)
            if search:
                query = query.where(Task.title.contains(search, autoescape=True))
            if group == "active":
                query = query.where(Task.status.not_in(TERMINAL))
            elif group == "history":
                query = query.where(Task.status.in_(TERMINAL))
            count = session.scalar(select(func.count()).select_from(query.subquery()))
            rows = session.scalars(query.order_by(Task.created_at.desc()).offset(offset).limit(limit))
            return {"items": [self.db.view(row, self.progress.get(row.id)) for row in rows], "total": count}

    def publish(self, task_id):
        event = {"type": "task", "task": self.get(task_id).model_dump(mode="json")}
        for queue in list(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def create(self, request):
        with Session(self.db.engine) as session:
            old = session.scalar(select(Task).where(Task.request_id == request.request_id))
            if old:
                return self.get(old.id)
            settings = self.db.settings()
            task_id = str(uuid.uuid4())
            source = request.source.model_dump()
            allow_invalid_tls = effective_invalid_tls(request.allow_invalid_tls)
            if request.context and (
                request.context.cookies
                or any("authorization" in context.headers for context in request.context.requests)
            ):
                source["requires_session"] = True
            session.add(
                Task(
                    id=task_id,
                    request_id=request.request_id,
                    title=source.get("title") or "正在获取视频信息…",
                    source_json=json.dumps(source),
                    status="queued",
                    quality=request.quality or settings["quality"],
                    format_id=request.format_id,
                    allow_invalid_tls=allow_invalid_tls,
                    downloaded_bytes=0,
                    target_dir=settings["download_dir"],
                    created_at=now(),
                    updated_at=now(),
                )
            )
            session.commit()
        if request.context:
            self.contexts[task_id] = request.context.model_dump()
        self.start_tls_diagnostic(task_id, source["url"])
        self.publish(task_id)
        self.wakeup.set()
        return self.get(task_id)

    async def cancel(self, task_id):
        self.get(task_id)
        if self.db.get(task_id).status in TERMINAL:
            return self.get(task_id)
        # Set terminal state first, so a late worker message cannot overwrite cancellation.
        self.db.update(task_id, status="canceled")
        if job := self.running.get(task_id):
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        self.contexts.pop(task_id, None)
        self.progress.pop(task_id, None)
        self.publish(task_id)
        self.wakeup.set()
        return self.get(task_id)

    async def delete(self, task_id, delete_file=False):
        row = self.db.get(task_id)
        if diagnostic := self.tls_jobs.pop(task_id, None):
            diagnostic.cancel()
            await asyncio.gather(diagnostic, return_exceptions=True)
        if row is None:
            raise KeyError(task_id)
        if row.status not in TERMINAL:
            await self.cancel(task_id)
        elif job := self.running.get(task_id):
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)

        row = self.db.get(task_id)
        work_root = (self.config.data_dir / "work").resolve()
        work = (work_root / task_id).resolve()
        if work.parent != work_root:
            raise TaskError("TASK_DELETE_FAILED")
        try:
            if work.exists():
                shutil.rmtree(work)
            if delete_file and row.output_path:
                output = Path(row.output_path)
                if output.exists():
                    output.unlink()
        except OSError as exc:
            log.warning("task=%s delete_error=%s", task_id, type(exc).__name__)
            raise TaskError("TASK_DELETE_FAILED") from None

        self.contexts.pop(task_id, None)
        self.progress.pop(task_id, None)
        if not self.db.delete(task_id):
            raise KeyError(task_id)
        self.wakeup.set()
        return {"ok": True}

    def retry(self, task_id, request):
        row = self.db.get(task_id)
        if row is None:
            raise KeyError(task_id)
        if row.status not in {"failed", "canceled", "interrupted"}:
            raise TaskError("TASK_NOT_RETRYABLE")
        source = request.source.model_dump() if request.source else json.loads(row.source_json)
        allow_invalid_tls = (
            effective_invalid_tls(request.allow_invalid_tls)
            if request.source
            else request.allow_invalid_tls
            if request.allow_invalid_tls is not None
            else row.allow_invalid_tls
        )
        if request.context and (
            request.context.cookies
            or any("authorization" in context.headers for context in request.context.requests)
        ):
            source["requires_session"] = True
        if source.get("requires_session") and not request.context:
            # Retry the captured URL even after its ephemeral browser context has expired.
            # Signed public URLs may still work; a truly protected source will return AUTH_REQUIRED.
            self.contexts[task_id] = {}
        if request.context:
            self.contexts[task_id] = request.context.model_dump()
        self.progress.pop(task_id, None)
        self.db.update(
            task_id,
            status="queued",
            source_json=json.dumps(source),
            allow_invalid_tls=allow_invalid_tls,
            error_json=None,
            downloaded_bytes=0,
            total_bytes=None,
            output_path=None,
            height=None,
        )
        self.start_tls_diagnostic(task_id, source["url"])
        self.publish(task_id)
        self.wakeup.set()
        return self.get(task_id)

    def resolve(self, request):
        stamp = time.monotonic()
        self.resolutions = {k: v for k, v in self.resolutions.items() if stamp - v[0] < 1800}
        if len(self.resolution_jobs) >= 8:
            raise TaskError("TOO_MANY_RESOLUTIONS", 429)
        resolution_id = str(uuid.uuid4())
        result = Resolution(id=resolution_id, status="resolving")
        self.resolutions[resolution_id] = (stamp, result)
        job = asyncio.create_task(self.run_resolution(resolution_id, request))
        self.resolution_jobs.add(job)
        job.add_done_callback(self.resolution_jobs.discard)
        return result

    async def run_resolution(self, resolution_id, request):
        wrapper = None
        result = self.resolutions[resolution_id][1]
        try:
            async with self.resolution_slots:
                async with asyncio.timeout(90):
                    wrapper = await WorkerProcess.start(
                        {
                            "action": "resolve",
                            "source": request.source.model_dump(),
                            "context": request.context.model_dump() if request.context else {},
                            "allow_invalid_tls": effective_invalid_tls(request.allow_invalid_tls),
                            "ffmpeg_dir": self.config.ffmpeg_dir,
                        }
                    )
                    async for line in wrapper.process.stdout:
                        event = json.loads(line)
                        if event["type"] == "result":
                            result.media = ResolvedMedia.model_validate(event["media"])
                            result.status = "completed"
                        elif event["type"] == "error":
                            result.error = ErrorInfo.model_validate(event["error"])
                            result.status = "failed"
                    await wrapper.process.wait()
                    if result.status == "resolving":
                        raise RuntimeError("worker exited")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result.status = "failed"
            result.error = ErrorInfo.model_validate(
                error_info("RESOLUTION_TIMEOUT" if isinstance(exc, TimeoutError) else "WORKER_FAILED")
            )
        finally:
            if wrapper:
                await wrapper.stop()

    async def schedule(self):
        while not self.stopping:
            self.wakeup.clear()
            available = self.db.settings()["concurrency"] - len(self.running)
            if available > 0:
                with Session(self.db.engine) as session:
                    ids = list(
                        session.scalars(
                            select(Task.id)
                            .where(Task.status == "queued")
                            .order_by(Task.created_at)
                            .limit(available)
                        )
                    )
                for task_id in ids:
                    if task_id not in self.running:
                        self.running[task_id] = asyncio.create_task(self.run_task(task_id))
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=1)
            except TimeoutError:
                pass

    async def run_task(self, task_id):
        wrapper = None
        last_saved = 0
        try:
            row = self.db.get(task_id)
            source = json.loads(row.source_json)
            if source.get("requires_session") and task_id not in self.contexts:
                raise TaskError("SESSION_REQUIRED")
            if not self.config.binary("ffmpeg") or not self.config.binary("ffprobe"):
                raise TaskError("DEPENDENCY_MISSING")
            fingerprint = hashlib.sha256(
                (source["url"] + str(row.format_id) + row.quality + str(row.allow_invalid_tls)).encode()
            ).hexdigest()[:16]
            work = self.config.data_dir / "work" / task_id / fingerprint
            self.db.update(task_id, status="resolving")
            self.publish(task_id)
            wrapper = await WorkerProcess.start(
                {
                    "action": "download",
                    "source": source,
                    "context": self.contexts.get(task_id, {}),
                    "quality": row.quality,
                    "format_id": row.format_id,
                    "allow_invalid_tls": row.allow_invalid_tls,
                    "work_dir": str(work),
                    "ffmpeg_dir": self.config.ffmpeg_dir,
                    "ffprobe": self.config.binary("ffprobe"),
                }
            )
            async for line in wrapper.process.stdout:
                event = json.loads(line)
                if self.db.get(task_id).status in TERMINAL:
                    continue
                kind = event["type"]
                if kind == "progress":
                    self.progress[task_id] = {
                        k: event[k] for k in ("downloaded_bytes", "total_bytes", "speed", "eta")
                    }
                    if time.monotonic() - last_saved > 2:
                        self.db.update(
                            task_id,
                            downloaded_bytes=event["downloaded_bytes"],
                            total_bytes=event["total_bytes"],
                        )
                        last_saved = time.monotonic()
                elif kind == "stage":
                    self.db.update(task_id, status=event["status"])
                    if event["status"] == "merging":
                        self.progress.pop(task_id, None)
                elif kind == "metadata":
                    self.db.update(task_id, title=event["title"], height=event.get("height"))
                elif kind == "error":
                    self.db.update(task_id, status="failed", error_json=json.dumps(event["error"]))
                elif kind == "complete":
                    artifact = Path(event["artifact"]).resolve()
                    if artifact.parent != work.resolve() or not artifact.is_file():
                        raise TaskError("INCOMPLETE_MEDIA")
                    current = self.db.get(task_id)
                    directory = Path(current.target_dir)
                    directory.mkdir(parents=True, exist_ok=True)
                    basename = sanitize_filename(current.title, restricted=False, is_id=False)[:120] or "视频"
                    destination = directory / (basename + artifact.suffix)
                    count = 1
                    while destination.exists():
                        destination = directory / f"{basename} ({count}){artifact.suffix}"
                        count += 1
                    # Copy via an exclusive temporary file, then rename on the destination volume.
                    temporary = directory / f".ergou-{task_id}.part"
                    try:
                        with artifact.open("rb") as source_file, temporary.open("wb") as output_file:
                            while chunk := source_file.read(1024 * 1024):
                                output_file.write(chunk)
                                await asyncio.sleep(0)
                        # Another task may have committed an identically titled video while copying.
                        while destination.exists():
                            destination = directory / f"{basename} ({count}){artifact.suffix}"
                            count += 1
                        temporary.rename(destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                    artifact.unlink()
                    self.progress.pop(task_id, None)
                    self.db.update(
                        task_id,
                        status="completed",
                        output_path=str(destination),
                        downloaded_bytes=event["size"],
                        total_bytes=event["size"],
                        height=event.get("height"),
                    )
                self.publish(task_id)
            await wrapper.process.wait()
            if self.db.get(task_id).status not in TERMINAL:
                raise TaskError("WORKER_FAILED")
        except asyncio.CancelledError:
            if self.db.get(task_id).status != "canceled":
                self.db.update(task_id, status="interrupted")
        except Exception as exc:
            code = (
                exc.code
                if isinstance(exc, TaskError)
                else "DISK_ERROR"
                if isinstance(exc, OSError)
                else "WORKER_FAILED"
            )
            self.db.update(task_id, status="failed", error_json=json.dumps(error_info(code)))
            log.warning("task=%s error=%s", task_id, code)
        finally:
            if wrapper:
                await wrapper.stop()
            self.contexts.pop(task_id, None)
            self.progress.pop(task_id, None)
            self.running.pop(task_id, None)
            self.publish(task_id)
            self.wakeup.set()
