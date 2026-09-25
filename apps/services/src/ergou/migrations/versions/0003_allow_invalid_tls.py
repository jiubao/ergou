"""Persist the per-task invalid HTTPS certificate compatibility option."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column(
        "download_tasks",
        sa.Column("allow_invalid_tls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("download_tasks", "allow_invalid_tls")
