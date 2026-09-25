"""Isolated yt-dlp runner. stdin: one JSON request; stdout: NDJSON events only."""

import http.cookiejar
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.networking._requests import RequestsRH

from .errors import classify_error, error_info

OUTPUT = sys.stdout


def emit(kind, **values):
    OUTPUT.write(json.dumps({"type": kind, **values}, ensure_ascii=True, allow_nan=False) + "\n")
    OUTPUT.flush()


class QuietLogger:
    def debug(self, _):
        pass

    def info(self, _):
        pass

    def warning(self, _):
        pass

    def error(self, _):
        pass


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port


class ScopedYoutubeDL(yt_dlp.YoutubeDL):
    def __init__(self, options, context):
        self.request_context = context.get("requests", [])
        super().__init__(options)

    def build_request_director(self, handlers, preferences=None):
        # Requests strips authentication on cross-origin redirects; exclude urllib's fallback,
        # which can retain an explicit Authorization header across a redirect.
        return super().build_request_director([RequestsRH], preferences)

    def process_video_result(self, info_dict, download=True):
        formats = info_dict.get("formats") or []
        # HLS may omit CODECS while explicitly declaring an external audio group. Normalize
        # those variants before yt-dlp's selector decides whether a second audio track is needed.
        manifests = {
            f.get("manifest_url")
            for f in formats
            if f.get("vcodec") == "none" and "m3u8" in f.get("protocol", "")
        }
        for manifest in manifests - {None}:
            with self.urlopen(Request(manifest)) as response:
                lines = response.read(4 * 1024 * 1024).decode("utf-8-sig", errors="replace").splitlines()
            external_groups = set()
            for line in lines:
                if line.startswith("#EXT-X-MEDIA:") and "TYPE=AUDIO" in line and 'URI="' in line:
                    group = re.search(r'GROUP-ID="([^"]+)"', line)
                    if group:
                        external_groups.add(group[1])
            variant_urls = set()
            pending_group = None
            for line in lines:
                if line.startswith("#EXT-X-STREAM-INF:"):
                    group = re.search(r'AUDIO="([^"]+)"', line)
                    pending_group = group[1] if group else None
                elif line and not line.startswith("#"):
                    if pending_group in external_groups:
                        variant_urls.add(urljoin(manifest, line.strip()))
                    pending_group = None
            for fmt in formats:
                if fmt.get("url") in variant_urls and fmt.get("acodec") is None:
                    fmt["acodec"] = "none"
        return super().process_video_result(info_dict, download)

    def urlopen(self, request):
        if isinstance(request, str):
            request = Request(request)
        url = request.url if hasattr(request, "url") else request.get_full_url()
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("UNSUPPORTED")
        headers = request.headers
        for ctx in self.request_context:
            if origin(url) == origin(ctx["url"]):
                for key, value in ctx["headers"].items():
                    headers[key] = value
        return super().urlopen(request)


def add_cookies(ydl, context, source):
    ydl.cookiejar.set_policy(
        http.cookiejar.DefaultCookiePolicy(
            strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain
        )
    )
    hosts = {urlsplit(source["url"]).hostname, urlsplit(source.get("page_url") or source["url"]).hostname}
    hosts.update(urlsplit(c["url"]).hostname for c in context.get("requests", []))
    for c in context.get("cookies", []):
        domain = c["domain"].lstrip(".").lower()
        if not domain or not any(h == domain or h.endswith("." + domain) for h in hosts if h):
            continue
        cookie = http.cookiejar.Cookie(
            version=0,
            name=c["name"],
            value=c["value"],
            port=None,
            port_specified=False,
            domain=c["domain"],
            domain_specified=not c.get("host_only", False),
            domain_initial_dot=c["domain"].startswith("."),
            path=c.get("path", "/"),
            path_specified=True,
            secure=c.get("secure", False),
            expires=int(c["expires"]) if c.get("expires") else None,
            discard=not c.get("expires"),
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        ydl.cookiejar.set_cookie(cookie)


def ensure_supported(ydl, info):
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        raise ValueError("LIVE_UNSUPPORTED")
    if info.get("has_drm"):
        raise ValueError("DRM_UNSUPPORTED")
    # Inspect known manifests. A blob or fragment alone never becomes a successful download.
    manifests = {}
    streams = info.get("requested_formats") or [info]
    for stream in streams:
        if stream.get("has_drm"):
            raise ValueError("DRM_UNSUPPORTED")
        url = stream.get("manifest_url") or stream.get("url", "")
        protocol = stream.get("protocol", "")
        if not url or not any(x in protocol for x in ("m3u8", "dash")):
            continue
        if url not in manifests:
            with ydl.urlopen(Request(url)) as response:
                manifests[url] = response.read(4 * 1024 * 1024)
        raw = manifests[url]
        text = raw.decode("utf-8-sig", errors="replace")
        if text.lstrip().startswith("#EXTM3U"):
            for line in text.splitlines():
                if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")) and "METHOD=NONE" not in line:
                    raise ValueError("DRM_UNSUPPORTED")
            if "#EXTINF" in text and "#EXT-X-ENDLIST" not in text:
                raise ValueError("LIVE_UNSUPPORTED")
            # A selected variant can contain encryption absent from the master.
            if stream.get("url") and stream["url"] != url and "m3u8" in protocol:
                with ydl.urlopen(Request(stream["url"])) as response:
                    variant = response.read(4 * 1024 * 1024).decode("utf-8-sig", errors="replace")
                if any(
                    line.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in line
                    for line in variant.splitlines()
                ):
                    raise ValueError("DRM_UNSUPPORTED")
                if "#EXTINF" in variant and "#EXT-X-ENDLIST" not in variant:
                    raise ValueError("LIVE_UNSUPPORTED")
        elif "dash" in protocol:
            root = ET.fromstring(raw)
            if root.get("type") == "dynamic":
                raise ValueError("LIVE_UNSUPPORTED")
            if any(e.tag.endswith("ContentProtection") for e in root.iter()):
                raise ValueError("DRM_UNSUPPORTED")


def prepare_resume(ydl, info, work):
    """Reuse partial data only when the upstream identity is unchanged."""
    identities = []
    for stream in info.get("requested_formats") or [info]:
        protocol = stream.get("protocol", "")
        url = stream.get("manifest_url") or stream.get("url")
        if not url:
            identities.append(str(uuid.uuid4()))
            continue
        if any(kind in protocol for kind in ("m3u8", "dash")):
            urls = {url, stream.get("url", url)} if "m3u8" in protocol else {url}
            for manifest in sorted(urls):
                with ydl.urlopen(Request(manifest)) as response:
                    identities.append(hashlib.sha256(response.read(4 * 1024 * 1024)).hexdigest())
        else:
            with ydl.urlopen(Request(url, headers={"Range": "bytes=0-0"})) as response:
                etag = response.headers.get("ETag")
                modified = response.headers.get("Last-Modified")
                size = response.headers.get("Content-Range", "").split("/")[-1] or response.headers.get(
                    "Content-Length"
                )
                # A weak ETag is not a byte-identity guarantee. Without validators, restart safely.
                validator = etag if etag and not etag.startswith("W/") else modified
                identities.append(f"{validator}:{size}" if validator else str(uuid.uuid4()))
    identity = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
    marker = work / "identity"
    previous = marker.read_text() if marker.exists() else None
    if previous != identity:
        for item in work.iterdir():
            if item.is_file() and item.name.startswith("media."):
                item.unlink()
    marker.write_text(identity)


def selector(payload):
    explicit = payload.get("format_id")
    if explicit:
        # IDs originate in the resolver; constrain the format expression grammar.
        if not re.fullmatch(r"[\w.:-]+(?:\+[\w.:-]+)?", explicit):
            raise ValueError("UNSUPPORTED")
        return explicit
    quality = payload.get("quality", "best")
    limit = f"[height<={int(quality)}]" if quality != "best" else ""
    return f"bv*{limit}+ba/b{limit}/b"


def media_view(info, source):
    if info.get("_type") in ("playlist", "multi_video") or "entries" in info:
        entries = []
        for entry in list(info.get("entries") or [])[:50]:
            if not entry:
                continue
            url = entry.get("webpage_url") or entry.get("url")
            if url and urlsplit(url).scheme in ("http", "https"):
                entries.append(
                    {
                        "url": url,
                        "page_url": source.get("page_url") or source["url"],
                        "title": entry.get("title"),
                        "kind": "page",
                        "requires_session": source.get("requires_session", False),
                    }
                )
        return {"title": info.get("title") or "页面中的视频", "entries": entries, "formats": []}
    formats = []
    audio = next(
        (
            f
            for f in reversed(info.get("formats", []))
            if f.get("vcodec") == "none" and f.get("acodec") != "none" and not f.get("has_drm")
        ),
        None,
    )
    for f in info.get("formats", [info]):
        if f.get("vcodec") == "none" or f.get("has_drm"):
            continue
        fmt_id = str(f.get("format_id", "best"))
        size = f.get("filesize") or f.get("filesize_approx")
        if f.get("acodec") == "none" and audio:
            fmt_id += "+" + str(audio["format_id"])
            size = size + audio["filesize"] if size and audio.get("filesize") else None
        height = f.get("height")
        formats.append(
            {
                "id": fmt_id,
                "label": f"{str(height) + 'p' if height else '原始画质'} · {f.get('ext', '')}",
                "height": height,
                "ext": f.get("ext"),
                "filesize": size,
            }
        )
    return {
        "title": info.get("title") or source.get("title") or "视频",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "formats": list(reversed(formats)),
        "entries": [],
    }


def finite(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def run(payload):
    source = payload["source"]
    context = payload.get("context") or {}
    action = payload["action"]
    progress_time = 0.0
    stream_bytes = {}
    stream_totals = {}

    def progress(data):
        nonlocal progress_time
        key = data.get("filename", "default")
        stream_bytes[key] = int(data.get("downloaded_bytes", 0))
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        stream_totals[key] = int(total) if finite(total) else None
        if time.monotonic() - progress_time < 0.3 and data["status"] != "finished":
            return
        progress_time = time.monotonic()
        emit(
            "progress",
            downloaded_bytes=sum(stream_bytes.values()),
            total_bytes=sum(v for v in stream_totals.values() if v) or None,
            speed=finite(data.get("speed")),
            eta=finite(data.get("eta")),
        )

    options = {
        "logger": QuietLogger(),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "playlistend": 50,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": False,
        "continuedl": True,
        "concurrent_fragment_downloads": 2,
        "format": selector(payload),
        "ffmpeg_location": payload.get("ffmpeg_dir"),
        "js_runtimes": {"node": {}},
        "remote_components": [],
        "progress_hooks": [progress],
        "postprocessor_hooks": [
            lambda d: emit("stage", status="merging") if d["status"] == "started" else None
        ],
        "merge_output_format": "mp4/mkv",
        "windowsfilenames": True,
        "overwrites": False,
        "hls_prefer_native": True,
        "nocheckcertificate": bool(payload.get("allow_invalid_tls", False)),
    }
    work = Path(payload.get("work_dir", "."))
    if action == "download":
        work.mkdir(parents=True, exist_ok=True)
        options["outtmpl"] = str(work / "media.%(ext)s")
    with ScopedYoutubeDL(options, context) as ydl:
        add_cookies(ydl, context, source)
        info = ydl.extract_info(source["url"], download=False)
        if not info:
            raise ValueError("UNSUPPORTED")
        if "entries" in info:
            if action == "resolve":
                emit("result", media=media_view(info, source))
                return
            raise ValueError("MULTIPLE_ENTRIES")
        ensure_supported(ydl, info)
        view = media_view(info, source)
        if action == "resolve":
            emit("result", media=view)
            return
        emit("metadata", title=source.get("title") or view["title"], height=info.get("height"))
        prepare_resume(ydl, info, work)
        emit("stage", status="downloading")
        ydl.process_info(info)
        emit("stage", status="merging")
        # Locate the actual merged artifact; never treat .part or fragment files as completed media.
        candidates = [
            p
            for p in work.glob("media.*")
            if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".ts")
            and re.fullmatch(r"media\.[^.]+", p.name)
        ]
        if len(candidates) != 1:
            raise ValueError("INCOMPLETE_MEDIA")
        artifact = candidates[0]
        ffprobe = payload.get("ffprobe") or shutil.which("ffprobe")
        if not ffprobe:
            raise ValueError("DEPENDENCY_MISSING")
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(artifact)],
            capture_output=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if probe.returncode:
            raise ValueError("INCOMPLETE_MEDIA")
        metadata = json.loads(probe.stdout)
        streams = metadata.get("streams", [])
        if not any(s.get("codec_type") == "video" for s in streams):
            raise ValueError("INCOMPLETE_MEDIA")
        expected_audio = info.get("acodec") not in (None, "none") or any(
            f.get("vcodec") == "none" or f.get("acodec") not in (None, "none")
            for f in info.get("requested_formats", [])
        )
        if expected_audio and not any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("INCOMPLETE_MEDIA")
        expected_duration = info.get("duration")
        actual_duration = float(metadata.get("format", {}).get("duration", 0))
        if expected_duration and actual_duration < expected_duration - max(2, expected_duration * 0.02):
            raise ValueError("INCOMPLETE_MEDIA")
        height = max((s.get("height", 0) for s in streams if s.get("codec_type") == "video"), default=0)
        emit("complete", artifact=str(artifact), size=artifact.stat().st_size, height=height or None)


if __name__ == "__main__":
    try:
        run(json.loads(sys.stdin.readline()))
    except Exception as exc:
        emit("error", error=error_info(classify_error(exc)))
        sys.exit(1)
