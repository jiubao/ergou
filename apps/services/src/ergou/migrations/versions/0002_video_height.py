"""Persist actual selected/probed video height independently of quality preference."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.add_column("download_tasks", sa.Column("height", sa.Integer()))


def downgrade():
    op.drop_column("download_tasks", "height")
