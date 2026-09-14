import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import shutil

import uvicorn

from .app import create_app
from .config import Config


def main():
    parser = argparse.ArgumentParser(description="Ergou 本地视频下载服务")
    parser.add_argument("command", nargs="?", choices=["serve", "doctor", "token"], default="serve")
    parser.add_argument("--no-token", action="store_true", help="Do not print the token when serving")
    args = parser.parse_args()
    config = Config.load()
    config.prepare()
    if args.command == "token":
        print(config.token)
        return
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "ffmpeg": config.binary("ffmpeg"),
                    "ffprobe": config.binary("ffprobe"),
                    "node": shutil.which("node"),
                    "data_dir": str(config.data_dir),
                    "web_built": bool(config.web_dir and (config.web_dir / "index.html").exists()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    handler = RotatingFileHandler(
        config.data_dir / "logs" / "service.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("ergou")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    print(f"Ergou: http://127.0.0.1:{config.port}", flush=True)
    if not args.no_token:
        print(f"Access token: {config.token}", flush=True)
    uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, access_log=False)


if __name__ == "__main__":
    main()
