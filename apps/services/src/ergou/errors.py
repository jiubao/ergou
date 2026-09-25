ERRORS = {
    "TASK_NOT_RETRYABLE": ("此任务当前不能重试", "仅失败、取消或中断的任务可以重试"),
    "TASK_DELETE_FAILED": ("无法删除任务文件", "关闭正在使用该文件的程序，或取消同时删除文件后重试"),
    "TOO_MANY_RESOLUTIONS": ("正在解析的视频过多", "请等待已有解析完成后再试"),
    "AUTH_REQUIRED": ("登录已失效，或资源需要新的授权信息", "返回原网页播放视频，再通过插件更新任务来源"),
    "SOURCE_EXPIRED": ("视频地址已过期或无法访问", "返回原网页重新识别，再更新任务来源"),
    "TLS_CERTIFICATE_ERROR": (
        "HTTPS 证书验证失败",
        "确认来源可信后，可为当前任务启用“允许无效 HTTPS 证书”再重试",
    ),
    "UNSUPPORTED": ("暂时无法解析此视频来源", "播放视频后重新识别，或选择另一个资源"),
    "DRM_UNSUPPORTED": ("此资源包含不支持的加密保护", "选择未加密的视频来源"),
    "LIVE_UNSUPPORTED": ("第一版暂不支持直播录制", "请选择已结束的点播视频"),
    "MULTIPLE_ENTRIES": ("此页面包含多个视频", "先解析页面，再选择一个视频下载"),
    "DEPENDENCY_MISSING": ("缺少 FFmpeg 或 ffprobe", "运行依赖检查脚本，安装工具后重试"),
    "DISK_ERROR": ("保存失败，磁盘空间或目录权限不足", "检查保存目录和可用空间后重试"),
    "NETWORK_ERROR": ("网络连接失败或服务器限流", "检查网络连接，稍后重试"),
    "INCOMPLETE_MEDIA": ("媒体不完整，或音画合并失败", "重试下载；持续失败时重新获取来源"),
    "RESOLUTION_TIMEOUT": ("视频解析超时", "播放视频后重试，或直接选择捕获到的媒体地址"),
    "WORKER_FAILED": ("下载进程异常退出", "重试任务，并检查服务依赖状态"),
    "SESSION_REQUIRED": ("此任务需要重新提供浏览器登录态", "返回原网页，通过插件更新此任务的来源"),
}


def error_info(code):
    message, action = ERRORS.get(code, ERRORS["WORKER_FAILED"])
    return {"code": code, "message": message, "action": action}


def classify_error(error):
    text = str(error).lower()
    if any(
        marker in text
        for marker in [
            "certificate_verify_failed",
            "certificateverifyerror",
            "certificate verify failed",
            "certificate has expired",
            "certificate is not yet valid",
            "self signed certificate",
            "self-signed certificate",
            "unable to get local issuer certificate",
            "hostname mismatch",
            "doesn't match",
        ]
    ):
        return "TLS_CERTIFICATE_ERROR"
    for code in sorted(ERRORS, key=len, reverse=True):
        if code.lower() in text:
            return code
    if any(x in text for x in ["401", "403", "login", "sign in", "cookies", "unauthorized"]):
        return "AUTH_REQUIRED"
    if any(x in text for x in ["404", "410", "expired"]):
        return "SOURCE_EXPIRED"
    if any(x in text for x in ["drm", "encrypted"]):
        return "DRM_UNSUPPORTED"
    if any(x in text for x in ["no space", "permission denied", "disk full", "winerror 112"]):
        return "DISK_ERROR"
    if any(x in text for x in ["timeout", "timed out", "connection", "429", "resolve", "network"]):
        return "NETWORK_ERROR"
    if any(x in text for x in ["ffmpeg", "ffprobe", "postprocessing", "fragment"]):
        return "INCOMPLETE_MEDIA"
    if any(x in text for x in ["unsupported", "no video", "no suitable", "unable to extract"]):
        return "UNSUPPORTED"
    return "WORKER_FAILED"
