"""Persist independent playback preparation state without changing downloaded files."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def upgrade():
    op.add_column(
        "download_tasks", sa.Column("playback_status", sa.String(), nullable=False, server_default="pending")
    )
    for name in ("playback_method", "playback_fingerprint", "playback_identity"):
        op.add_column("download_tasks", sa.Column(name, sa.String()))
    for name in ("playback_path", "playback_media_json", "playback_error_json"):
        op.add_column("download_tasks", sa.Column(name, sa.Text()))


def downgrade():
    with op.batch_alter_table("download_tasks") as batch:
        for name in (
            "playback_status",
            "playback_method",
            "playback_fingerprint",
            "playback_identity",
            "playback_path",
            "playback_media_json",
            "playback_error_json",
        ):
            batch.drop_column(name)
