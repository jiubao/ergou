"""Create download tasks and settings."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "download_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("request_id", sa.String(), nullable=False, unique=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("quality", sa.String(), nullable=False),
        sa.Column("format_id", sa.String()),
        sa.Column("downloaded_bytes", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer()),
        sa.Column("output_path", sa.Text()),
        sa.Column("target_dir", sa.Text(), nullable=False),
        sa.Column("error_json", sa.Text()),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_download_tasks_status", "download_tasks", ["status"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("download_tasks")
    op.drop_table("settings")
