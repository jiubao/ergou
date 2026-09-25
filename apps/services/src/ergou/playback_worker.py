"""Local media inspection and conversion, owned by a killable WorkerProcess job."""

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from .errors import TaskError, error_info

ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")


def fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    return hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()


def emit(kind, **values):
    print(json.dumps({"type": kind, **values}), flush=True)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def probe(binary, path):
    result = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    raw = json.loads(result.stdout)
    video = next(
        (
            s
            for s in raw.get("streams", [])
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video or not Path(path).stat().st_size:
        raise TaskError("PLAYBACK_PREPARATION_FAILED")
    return {
        "container": raw.get("format", {}).get("format_name", ""),
        "duration": number(raw.get("format", {}).get("duration")) or number(video.get("duration")),
        "video": video,
        "audio": audio,
    }


def method_for(path, media):
    video, audio = media["video"], media["audio"]
    vc, ac = video.get("codec_name"), audio.get("codec_name") if audio else None
    if video.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        return "transcode"
    # H.264 10-bit/4:4:4 profiles are not covered by normal browser AVC decoders.
    if vc == "h264" and video.get("pix_fmt") in {"yuv420p", "yuvj420p"} and ac in {None, "aac", "mp3"}:
        if Path(path).suffix.lower() in {".mp4", ".mov", ".m4v"} and "mp4" in media["container"]:
            return "original"
        return "remux"
    if (
        Path(path).suffix.lower() == ".webm"
        and "webm" in media["container"]
        and vc in {"vp8", "vp9", "av1"}
        and ac in {None, "opus", "vorbis"}
    ):
        return "original"
    return "transcode"


def encoder_options(encoder):
    return {
        "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
        "h264_qsv": ["-preset", "medium", "-global_quality", "21"],
        "h264_amf": ["-quality", "quality", "-rc", "cqp", "-qp_i", "21", "-qp_p", "21", "-qp_b", "23"],
        "libx264": ["-preset", "medium", "-crf", "21"],
    }[encoder]


def choose_encoder(ffmpeg):
    for encoder in ENCODERS:
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=128x128:rate=24",
                    "-frames:v",
                    "4",
                    "-an",
                    "-c:v",
                    encoder,
                    *encoder_options(encoder),
                    "-pix_fmt",
                    "nv12" if encoder == "h264_qsv" else "yuv420p",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return encoder
        except (subprocess.SubprocessError, OSError):
            continue
    return "libx264"


def video_filter(media, encoder):
    video = media["video"]
    filters = []
    if video.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        # Decode in software, linearize HDR, tone-map, then explicitly tag SDR output.
        transfer = video["color_transfer"]
        filters += [
            f"zscale=pin=bt2020:tin={transfer}:min=bt2020nc:t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=mobius:desat=2",
            "zscale=t=bt709:m=bt709:r=tv",
        ]
    filters += [
        "scale=ceil(iw/2)*2:ceil(ih/2)*2",
        "format=" + ("nv12" if encoder == "h264_qsv" else "yuv420p"),
    ]
    return ",".join(filters)


def progress_event(values, duration):
    position = number(values.get("out_time_us"))
    position = position / 1_000_000 if position is not None else None
    speed = number(values.get("speed", "").rstrip("x"))
    return {
        "playback_progress": min(99.0, position / duration * 100)
        if duration and position is not None
        else None,
        "playback_speed": speed,
        "playback_eta": max(0, duration - position) / speed
        if duration and speed and position is not None
        else None,
    }


def convert(ffmpeg, source, target, media, method, encoder):
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-map",
        f"0:{media['video']['index']}",
    ]
    if media["audio"]:
        cmd += ["-map", f"0:{media['audio']['index']}"]
    cmd += ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1"]
    if method == "remux":
        cmd += ["-c", "copy"]
    else:
        cmd += [
            "-vf",
            video_filter(media, encoder),
            "-c:v",
            encoder,
            *encoder_options(encoder),
            "-fps_mode",
            "passthrough",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
        if media["video"].get("color_transfer") in {"smpte2084", "arib-std-b67"}:
            cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(target)]
    # A file avoids a stderr pipe deadlock while stdout is consumed incrementally.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
        try:
            values = {}
            for line in process.stdout:
                key, _, value = line.strip().partition("=")
                values[key] = value
                if key == "progress":
                    emit("progress", **progress_event(values, media["duration"]))
                    values = {}
            if process.wait():
                errors.seek(max(0, errors.tell() - 8192))
                detail = errors.read().decode("utf-8", errors="replace").lower()
                code = (
                    "DISK_ERROR"
                    if any(s in detail for s in ("no space", "permission denied", "disk full"))
                    else "PLAYBACK_PREPARATION_FAILED"
                )
                raise TaskError(code)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def prepare(payload):
    source = Path(payload["source"])
    target = Path(payload["target"])
    media = probe(payload["ffprobe"], source)
    method = "transcode" if payload["mode"] == "transcode" else method_for(source, media)
    emit("metadata", media=media, method=method)
    if method == "original":
        emit("complete", status="ready_original")
        return
    if method == "transcode" and payload["mode"] != "transcode":
        emit("complete", status="transcode_required")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    encoder = None
    emit("stage", status="remuxing" if method == "remux" else "transcoding")
    if method == "transcode":
        encoder = payload.get("encoder") or choose_encoder(payload["ffmpeg"])
        emit("encoder", encoder=encoder)
    try:
        convert(payload["ffmpeg"], source, target, media, method, encoder)
    except TaskError as exc:
        if method != "transcode" or encoder == "libx264" or exc.code == "DISK_ERROR":
            raise
        target.unlink(missing_ok=True)
        encoder = "libx264"
        emit("encoder", encoder=encoder)
        emit("progress", playback_progress=0, playback_speed=None, playback_eta=None)
        convert(payload["ffmpeg"], source, target, media, method, encoder)
    result = probe(payload["ffprobe"], target)
    if method_for(target, result) != "original" or not result["duration"]:
        raise TaskError("PLAYBACK_PREPARATION_FAILED")
    if media["audio"] and not result["audio"]:
        raise TaskError("PLAYBACK_PREPARATION_FAILED")
    if media["duration"] and abs(result["duration"] - media["duration"]) > max(2, media["duration"] * 0.02):
        raise TaskError("PLAYBACK_PREPARATION_FAILED")
    emit("complete", status="ready_compatible")


def main():
    payload = json.loads(sys.stdin.readline())
    try:
        prepare(payload)
    except Exception as exc:
        code = exc.code if isinstance(exc, TaskError) else "PLAYBACK_PREPARATION_FAILED"
        emit("error", error=error_info(code))


if __name__ == "__main__":
    main()
