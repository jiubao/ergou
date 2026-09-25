import json
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
import trustme
from fastapi.testclient import TestClient

from ergou.app import create_app
from ergou.config import Config


@pytest.fixture
def config(tmp_path):
    cfg = Config(tmp_path / "data", web_dir=tmp_path / "web")
    cfg.prepare()
    return cfg


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        c.headers["Authorization"] = "Bearer " + config.token
        yield c


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    return build_media(tmp_path_factory.mktemp("media"))


def build_media(root):
    ffmpeg = Config.load().binary("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not found; run scripts/install-ffmpeg.ps1")
    root.mkdir(parents=True, exist_ok=True)

    def run(*args):
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
            check=True,
            capture_output=True,
            timeout=60,
            cwd=Path(args[-1]).parent,
        )

    run(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "6",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-g",
        "48",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        root / "sample.mp4",
    )
    run(
        "-i",
        root / "sample.mp4",
        "-c:v",
        "libvpx-vp9",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-c:a",
        "libopus",
        root / "sample.webm",
    )
    for name, mapping in [("hls", []), ("video", ["-map", "0:v"]), ("audio", ["-map", "0:a"])]:
        directory = root / name
        directory.mkdir(exist_ok=True)
        run(
            "-i",
            root / "sample.mp4",
            *mapping,
            "-c",
            "copy",
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_playlist_type",
            "vod",
            directory / "index.m3u8",
        )
    (root / "master.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Original",DEFAULT=YES,AUTOSELECT=YES,URI="audio/index.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=320x180,AUDIO="audio"\nvideo/index.m3u8\n',
        encoding="utf-8",
    )
    (root / "multi.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=320x180\nhls/index.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=320x180\nhls/index.m3u8\n",
        encoding="utf-8",
    )
    dash = root / "dash"
    dash.mkdir(exist_ok=True)
    run(
        "-i",
        root / "sample.mp4",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c",
        "copy",
        "-f",
        "dash",
        "-seg_duration",
        "2",
        dash / "index.mpd",
    )
    shutil.copytree(root / "hls", root / "missing", dirs_exist_ok=True)
    (root / "missing" / "index1.ts").unlink(missing_ok=True)
    (root / "live.m3u8").write_text(
        (root / "hls" / "index.m3u8").read_text().replace("#EXT-X-ENDLIST", "").replace("index", "hls/index"),
        encoding="utf-8",
    )
    (root / "encrypted.m3u8").write_text(
        '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:6,\nhls/index0.ts\n#EXT-X-ENDLIST\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="session")
def media_server(media_dir):
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(media_dir), **kwargs)

        def log_message(self, *_):
            pass

        def do_GET(self):
            route = urlsplit(self.path).path
            requests.append({"path": self.path, "headers": dict(self.headers)})
            if route.startswith("/auth/"):
                if (
                    "session=valid" not in self.headers.get("Cookie", "")
                    or self.headers.get("Referer") != "https://page.test/watch"
                ):
                    self.send_error(403)
                    return
                self.path = route.replace("/auth/", "/")
                route = self.path
            if route == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/sample.mp4")
                self.end_headers()
                return
            if route in ("/gone.mp4", "/limited.mp4"):
                self.send_error(410 if route == "/gone.mp4" else 429)
                return
            if route in ("/slow.mp4", "/stream"):
                data = (media_dir / "sample.mp4").read_bytes()
                range_header = self.headers.get("Range", "")
                start = int(range_header.split("=")[1].split("-")[0]) if range_header else 0
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(data) - start))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"fixture-video-v1"')
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                try:
                    for pos in range(start, len(data), 4096):
                        self.wfile.write(data[pos : pos + 4096])
                        self.wfile.flush()
                        if route == "/slow.mp4":
                            time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            return super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", requests
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def invalid_https_media_server(media_dir):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(media_dir), **kwargs)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ca = trustme.CA()
    certificate = ca.issue_cert("127.0.0.1")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(context)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def media_client(config, media_server, tmp_path):
    config.ffmpeg_dir = str(Path(Config.load().binary("ffmpeg")).parent)
    with TestClient(create_app(config)) as client:
        client.headers["Authorization"] = "Bearer " + config.token
        settings = client.get("/api/v1/settings").json()
        settings["download_dir"] = str(tmp_path / "downloads")
        client.patch("/api/v1/settings", json=settings).raise_for_status()
        yield client


def wait_task(client, task_id, states=None, timeout=90):
    states = states or {"completed", "failed", "canceled", "interrupted"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in states:
            return task
        time.sleep(0.1)
    raise AssertionError(f"Task timed out: {json.dumps(task)}")
