from dataclasses import dataclass
from pathlib import Path
import os
import secrets
import shutil

from platformdirs import user_data_path, user_downloads_path


@dataclass
class Config:
    data_dir: Path
    port: int = 17890
    ffmpeg_dir: str | None = None
    web_dir: Path | None = None

    @classmethod
    def load(cls):
        root = Path(__file__).resolve().parents[3]
        bundled_tools = root / ".tools" / "ffmpeg"
        return cls(
            data_dir=Path(os.environ.get("ERGOU_DATA_DIR", user_data_path("Ergou", appauthor=False))),
            port=int(os.environ.get("ERGOU_PORT", "17890")),
            ffmpeg_dir=os.environ.get("ERGOU_FFMPEG_DIR")
            or (str(bundled_tools) if bundled_tools.exists() else None),
            web_dir=Path(os.environ.get("ERGOU_WEB_DIR", root / "web" / "dist")),
        )

    def prepare(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "work").mkdir(exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)
        token_file = self.data_dir / "access-token"
        try:
            with token_file.open("x", encoding="utf-8") as f:
                f.write(secrets.token_urlsafe(32))
            token_file.chmod(0o600)
        except FileExistsError:
            pass

    @property
    def token(self):
        return (self.data_dir / "access-token").read_text(encoding="utf-8").strip()

    def binary(self, name: str):
        if self.ffmpeg_dir:
            path = Path(self.ffmpeg_dir) / (name + (".exe" if os.name == "nt" else ""))
            if path.is_file():
                return str(path)
        return shutil.which(name)


def default_settings():
    return {"download_dir": str(user_downloads_path() / "Ergou"), "quality": "best", "concurrency": 2}
