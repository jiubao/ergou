import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import re
from typing import Literal
import shutil
import subprocess

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .config import Config
from .errors import error_info
from .manager import Manager, TaskError
from .schemas import (
    CreateTask,
    Health,
    Resolution,
    ResolveRequest,
    RetryTask,
    Settings,
    TaskPage,
    TaskState,
    TaskView,
)


def create_app(config=None):
    cfg = config or Config.load()
    allowed = rf"^(http://(127\.0\.0\.1|localhost):({cfg.port}|5173)|chrome-extension://[a-p]{{32}})$"

    @asynccontextmanager
    async def lifespan(app):
        cfg.prepare()
        app.state.manager = Manager(cfg)
        await app.state.manager.start()
        try:
            yield
        finally:
            await app.state.manager.close()

    app = FastAPI(
        title="Ergou local API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=allowed,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def source_check(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and not re.fullmatch(allowed, origin):
            return JSONResponse({"detail": "不允许的请求来源"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https: http:; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def authorize(request: Request):
        value = request.headers.get("authorization", "")
        if not hmac.compare_digest(value, "Bearer " + cfg.token):
            raise HTTPException(401, "请先输入本地服务访问令牌")

    auth = [Depends(authorize)]

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": "记录不存在或已过期"}, status_code=404)

    @app.exception_handler(TaskError)
    async def task_error(_, exc):
        return JSONResponse({"detail": error_info(exc.code)}, status_code=exc.status)

    @app.get("/api/v1/health", response_model=Health)
    def health():
        return Health(
            version=__version__,
            ffmpeg=bool(cfg.binary("ffmpeg")),
            ffprobe=bool(cfg.binary("ffprobe")),
            node=bool(shutil.which("node")),
        )

    @app.get("/api/v1/schema", dependencies=auth)
    def schema():
        return app.openapi()

    @app.post("/api/v1/resolutions", response_model=Resolution, dependencies=auth, status_code=202)
    async def resolve(request: ResolveRequest):
        return app.state.manager.resolve(request)

    @app.get("/api/v1/resolutions/{resolution_id}", response_model=Resolution, dependencies=auth)
    async def resolution(resolution_id: str):
        return app.state.manager.resolutions[resolution_id][1]

    @app.post("/api/v1/tasks", response_model=TaskView, dependencies=auth, status_code=201)
    async def create(request: CreateTask):
        return app.state.manager.create(request)

    @app.get("/api/v1/tasks", response_model=TaskPage, dependencies=auth)
    async def tasks(
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        status: TaskState | None = None,
        search: str | None = Query(None, max_length=200),
        group: Literal["active", "history"] | None = None,
    ):
        return app.state.manager.listing(offset, limit, status, search, group)

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskView, dependencies=auth)
    async def task(task_id: str):
        return app.state.manager.get(task_id)

    @app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskView, dependencies=auth)
    async def cancel(task_id: str):
        return await app.state.manager.cancel(task_id)

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskView, dependencies=auth)
    async def retry(task_id: str, request: RetryTask):
        return app.state.manager.retry(task_id, request)

    def local_file(task_id):
        task = app.state.manager.get(task_id)
        if task.status != "completed" or not task.output_path:
            raise HTTPException(409, "任务尚未完成")
        path = Path(task.output_path)
        if not path.is_file():
            raise HTTPException(404, "文件已移动或删除")
        return path

    @app.post("/api/v1/tasks/{task_id}/open", dependencies=auth)
    async def open_file(task_id: str):
        path = local_file(task_id)
        if os.name == "nt":
            os.startfile(path)
        else:
            raise HTTPException(501, "第一版仅支持 Windows 文件操作")
        return {"ok": True}

    @app.post("/api/v1/tasks/{task_id}/reveal", dependencies=auth)
    async def reveal(task_id: str):
        path = local_file(task_id)
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        else:
            raise HTTPException(501, "第一版仅支持 Windows 文件操作")
        return {"ok": True}

    @app.get("/api/v1/settings", response_model=Settings, dependencies=auth)
    async def settings():
        return app.state.manager.db.settings()

    @app.patch("/api/v1/settings", response_model=Settings, dependencies=auth)
    async def update_settings(request: Settings):
        directory = Path(request.download_dir)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if not directory.is_dir():
                raise OSError()
        except OSError:
            raise HTTPException(422, "保存目录不可用") from None
        app.state.manager.db.update_settings(request.model_dump())
        app.state.manager.wakeup.set()
        return request

    @app.websocket("/api/v1/events")
    async def events(ws: WebSocket):
        if not re.fullmatch(allowed, ws.headers.get("origin", "")):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=256)
        try:
            data = await asyncio.wait_for(ws.receive_json(), timeout=5)
            if not isinstance(data, dict) or not hmac.compare_digest(str(data.get("token", "")), cfg.token):
                await ws.close(code=1008)
                return
            app.state.manager.subscribers.add(queue)
            await ws.send_json({"type": "ready"})

            # Read and write independently so disconnects are noticed even without progress events.
            async def send():
                while True:
                    event = await queue.get()
                    await ws.send_json(event)

            sender = asyncio.create_task(send())
            try:
                while True:
                    await ws.receive_text()
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        except (WebSocketDisconnect, TimeoutError, json.JSONDecodeError):
            pass
        finally:
            app.state.manager.subscribers.discard(queue)

    @app.get("/{asset:path}", include_in_schema=False)
    async def static(asset: str):
        if asset.startswith("api/"):
            raise HTTPException(404)
        root = cfg.web_dir
        if root and root.is_dir():
            path = (root / asset).resolve()
            if path.is_relative_to(root.resolve()) and path.is_file():
                return FileResponse(path)
            index = root / "index.html"
            if index.is_file():
                return FileResponse(index)
        return JSONResponse({"message": "服务已启动。请先运行 pnpm build，或使用 pnpm dev:web 开发界面。"})

    return app
