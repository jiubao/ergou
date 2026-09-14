import json
from pathlib import Path


def repository_root(start: Path | None = None) -> Path:
    """Find the Ergou workspace without relying on a fixed package depth."""
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        workspace = candidate / "pnpm-workspace.yaml"
        package = candidate / "package.json"
        if not workspace.is_file() or not package.is_file():
            continue
        try:
            manifest = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("name") == "ergou" and manifest.get("private") is True:
            return candidate
    raise RuntimeError("无法定位 Ergou 仓库根目录")
