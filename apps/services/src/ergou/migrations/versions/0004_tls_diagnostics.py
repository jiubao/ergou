"""Enable TLS compatibility by default and persist certificate diagnostics."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"


def upgrade():
    with op.batch_alter_table("download_tasks") as batch:
        batch.alter_column(
            "allow_invalid_tls",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )
        batch.add_column(
            sa.Column(
                "tls_certificate_status",
                sa.String(),
                nullable=False,
                server_default="unchecked",
            )
        )
    op.execute(sa.text("UPDATE download_tasks SET allow_invalid_tls = 1"))


def downgrade():
    with op.batch_alter_table("download_tasks") as batch:
        batch.drop_column("tls_certificate_status")
        batch.alter_column(
            "allow_invalid_tls",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        )
