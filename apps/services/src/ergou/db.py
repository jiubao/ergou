from datetime import datetime, timezone
from pathlib import Path
import json

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Boolean, Column, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Session

from .config import default_settings
from .errors import error_info
from .schemas import TaskView


def now():
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "download_tasks"
    id = Column(String, primary_key=True)
    request_id = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    source_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, index=True)
    quality = Column(String, nullable=False)
    format_id = Column(String)
    allow_invalid_tls = Column(Boolean, nullable=False, default=True)
    tls_certificate_status = Column(String, nullable=False, default="unchecked")
    height = Column(Integer)
    downloaded_bytes = Column(Integer, nullable=False, default=0)
    total_bytes = Column(Integer)
    output_path = Column(Text)
    target_dir = Column(Text, nullable=False)
    error_json = Column(Text)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)


class Database:
    def __init__(self, data_dir: Path):
        url = "sqlite:///" + (data_dir / "ergou.sqlite3").as_posix()
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10})

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")

        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        with Session(self.engine) as session:
            for key, value in default_settings().items():
                if session.get(Setting, key) is None:
                    session.add(Setting(key=key, value=json.dumps(value)))
            session.commit()

    def settings(self):
        with Session(self.engine) as session:
            return {row.key: json.loads(row.value) for row in session.scalars(select(Setting))}

    def update_settings(self, values):
        with Session(self.engine) as session:
            for key, value in values.items():
                session.merge(Setting(key=key, value=json.dumps(value)))
            session.commit()

    def get(self, task_id):
        with Session(self.engine) as session:
            return session.get(Task, task_id)

    def update(self, task_id, **values):
        with Session(self.engine) as session:
            row = session.get(Task, task_id)
            if row is None:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = now()
            session.commit()
            session.refresh(row)
            return row

    def delete(self, task_id):
        with Session(self.engine) as session:
            row = session.get(Task, task_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def recover(self):
        with Session(self.engine) as session:
            for row in session.scalars(
                select(Task).where(Task.status.in_(["queued", "resolving", "downloading", "merging"]))
            ):
                row.status = "interrupted"
                row.updated_at = now()
            session.commit()

    @staticmethod
    def view(row, progress=None):
        stored_error = json.loads(row.error_json) if row.error_json else None
        if stored_error and stored_error.get("code") == "TLS_CERTIFICATE_ERROR":
            stored_error = error_info(stored_error["code"])
        result = TaskView(
            id=row.id,
            title=row.title,
            source=json.loads(row.source_json),
            status=row.status,
            quality=row.quality,
            format_id=row.format_id,
            allow_invalid_tls=row.allow_invalid_tls,
            tls_certificate_status=row.tls_certificate_status,
            downloaded_bytes=row.downloaded_bytes,
            height=row.height,
            total_bytes=row.total_bytes,
            output_path=row.output_path,
            error=stored_error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        return result.model_copy(update=progress or {})
