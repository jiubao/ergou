from pathlib import Path

from ergou.config import Config
from ergou.workspace import repository_root


def test_repository_root_from_nested_service_path():
    root = repository_root(Path(__file__))
    assert (root / "apps" / "services" / "pyproject.toml").is_file()
    assert (root / "packages" / "contracts" / "openapi.json").is_file()


def test_config_uses_monorepo_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("ERGOU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ERGOU_WEB_DIR", raising=False)
    monkeypatch.delenv("ERGOU_FFMPEG_DIR", raising=False)
    root = repository_root()
    config = Config.load()
    assert config.web_dir == root / "apps" / "web" / "dist"
    if (root / ".tools" / "ffmpeg").exists():
        assert config.ffmpeg_dir == str(root / ".tools" / "ffmpeg")


def test_config_environment_paths_override_workspace_defaults(monkeypatch, tmp_path):
    web = tmp_path / "custom-web"
    ffmpeg = tmp_path / "custom-ffmpeg"
    monkeypatch.setenv("ERGOU_WEB_DIR", str(web))
    monkeypatch.setenv("ERGOU_FFMPEG_DIR", str(ffmpeg))
    config = Config.load()
    assert config.web_dir == web
    assert config.ffmpeg_dir == str(ffmpeg)
