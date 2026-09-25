from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_url(value: str):
    url = urlsplit(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("仅支持无内嵌账号密码的 HTTP/HTTPS 地址")
    if len(value) > 16384:
        raise ValueError("地址过长")
    return value


class Cookie(StrictModel):
    name: str = Field(max_length=1024)
    value: str = Field(max_length=16384)
    domain: str = Field(max_length=255)
    path: str = "/"
    secure: bool = False
    host_only: bool = False
    expires: float | None = None


class HeaderContext(StrictModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    _url = field_validator("url")(validate_url)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value):
        allowed = {"referer", "origin", "user-agent", "authorization", "accept", "accept-language"}
        if any(
            k.lower() not in allowed or "\r" in v or "\n" in v or len(v) > 16384 for k, v in value.items()
        ):
            raise ValueError("不支持的请求头")
        return {k.lower(): v for k, v in value.items()}


class SessionContext(StrictModel):
    cookies: list[Cookie] = Field(default_factory=list, max_length=500)
    requests: list[HeaderContext] = Field(default_factory=list, max_length=50)


class Source(StrictModel):
    url: str
    page_url: str | None = None
    title: str | None = Field(default=None, max_length=512)
    kind: Literal["direct", "hls", "dash", "page", "unknown"] = "unknown"
    requires_session: bool = False
    _url = field_validator("url")(validate_url)

    @field_validator("page_url")
    @classmethod
    def page(cls, value):
        return validate_url(value) if value else value


class ResolveRequest(StrictModel):
    source: Source
    context: SessionContext | None = None
    allow_invalid_tls: bool | None = None


class FormatOption(BaseModel):
    id: str
    label: str
    height: int | None = None
    ext: str | None = None
    filesize: int | None = None


class ResolvedMedia(BaseModel):
    title: str
    duration: float | None = None
    thumbnail: str | None = None
    formats: list[FormatOption] = Field(default_factory=list)
    entries: list[Source] = Field(default_factory=list)


class ErrorInfo(BaseModel):
    code: str
    message: str
    action: str


class Resolution(BaseModel):
    id: str
    status: Literal["resolving", "completed", "failed"]
    media: ResolvedMedia | None = None
    error: ErrorInfo | None = None


class CreateTask(ResolveRequest):
    request_id: str = Field(min_length=8, max_length=100)
    format_id: str | None = Field(default=None, max_length=200)
    quality: Literal["best", "1080", "720", "480"] | None = None


class RetryTask(StrictModel):
    source: Source | None = None
    context: SessionContext | None = None
    allow_invalid_tls: bool | None = None


class TaskState(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


PlaybackState = Literal[
    "pending",
    "checking",
    "ready_original",
    "remuxing",
    "transcode_required",
    "transcode_queued",
    "transcoding",
    "ready_compatible",
    "canceled",
    "interrupted",
    "failed",
]


class PreparePlayback(StrictModel):
    mode: Literal["recommended", "transcode"] = "recommended"


class CreatePlayback(StrictModel):
    source: Literal["preferred", "original"] = "preferred"


class TaskView(BaseModel):
    id: str
    title: str
    source: Source
    status: TaskState
    quality: str
    format_id: str | None
    allow_invalid_tls: bool = True
    tls_certificate_status: Literal[
        "unchecked", "checking", "valid", "invalid", "unavailable", "not_applicable"
    ] = "unchecked"
    height: int | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None
    output_path: str | None = None
    error: ErrorInfo | None = None
    playback_status: PlaybackState = "pending"
    playback_method: Literal["original", "remux", "transcode"] | None = None
    playback_progress: float | None = None
    playback_speed: float | None = None
    playback_eta: float | None = None
    playback_error: ErrorInfo | None = None
    playback_identity: str | None = None
    created_at: str
    updated_at: str


class TaskPage(BaseModel):
    items: list[TaskView]
    total: int


class PlaybackSession(BaseModel):
    url: str
    expires_at: str
    playback_identity: str


class Settings(StrictModel):
    download_dir: str
    quality: Literal["best", "1080", "720", "480"] = "best"
    concurrency: int = Field(default=2, ge=1, le=4)

    @field_validator("download_dir")
    @classmethod
    def directory(cls, value):
        if not Path(value).is_absolute():
            raise ValueError("保存目录必须是绝对路径")
        return value


class Health(BaseModel):
    version: str
    protocol_version: int = 1
    ffmpeg: bool
    ffprobe: bool
    node: bool


class TaskEvent(BaseModel):
    type: Literal["task", "ready"]
    task: TaskView | None = None
