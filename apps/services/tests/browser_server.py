"""Isolated local fixtures and service for browser integration tests."""

import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
from urllib.parse import urlsplit

import uvicorn

from conftest import build_media
from ergou.app import create_app
from ergou.config import Config
from ergou.db import Database
from ergou.workspace import repository_root

ROOT = repository_root()
DATA = ROOT / ".local" / "browser-tests"
MEDIA = DATA / "media"
SERVICE_PORT = 17894
MEDIA_PORT = 17895
os.environ["ERGOU_PORT"] = str(SERVICE_PORT)
os.environ["ERGOU_DATA_DIR"] = str(DATA / "data")
build_media(MEDIA)

WATCH = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本地视频验证</title>
<style>body{background:#eef1e7;color:#243c2a;font:16px sans-serif;padding:40px}video{width:480px;display:block;margin:20px 0}</style>
<h1>本地视频验证</h1><p>这个页面用于验证插件发现、登录态与下载。</p>
<video id="main-video" controls preload="metadata" title="MP4 完整下载验证" src="/sample.mp4"></video>
<button id="add-video" onclick="let v=document.createElement('video');v.controls=true;v.src='/sample.webm';v.title='动态加载 WebM';document.body.append(v)">添加动态视频</button>
<button id="load-hls" onclick="Promise.all([fetch('/multi.m3u8'),fetch('/hls/index.m3u8')])">加载 HLS 清单</button>
<iframe src="http://localhost:17895/frame.html"></iframe></html>"""
(MEDIA / "watch.html").write_text(WATCH, encoding="utf-8")
(MEDIA / "frame.html").write_text(
    '<!doctype html><meta charset="utf-8"><title>嵌入视频</title><video controls preload="metadata" title="iframe 视频" src="/sample.webm"></video>',
    encoding="utf-8",
)
(MEDIA / "auth.html").write_text(
    '<!doctype html><meta charset="utf-8"><title>登录视频验证</title>'
    '<video controls preload="metadata" title="登录态完整下载验证" src="/auth/sample.mp4"></video>',
    encoding="utf-8",
)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        route = urlsplit(self.path).path
        if route.startswith("/auth/"):
            if (
                "session=browser-valid" not in self.headers.get("Cookie", "")
                or self.headers.get("Referer") != f"http://127.0.0.1:{MEDIA_PORT}/auth.html"
            ):
                self.send_error(403)
                return
            self.path = route.replace("/auth/", "/")
        return super().do_GET()


server = ThreadingHTTPServer(("127.0.0.1", MEDIA_PORT), functools.partial(Handler, directory=str(MEDIA)))
threading.Thread(target=server.serve_forever, daemon=True).start()
config = Config.load()
config.prepare()
(config.data_dir / "access-token").write_text("ergou-browser-test-token-ephemeral", encoding="utf-8")
db = Database(config.data_dir)
db.update_settings({"download_dir": str(DATA / "downloads"), "concurrency": 2})
db.engine.dispose()
uvicorn.run(create_app(config), host="127.0.0.1", port=SERVICE_PORT, access_log=False)
