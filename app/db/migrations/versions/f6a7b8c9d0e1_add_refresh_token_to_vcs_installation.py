"""Add refresh_token to vcs_installation.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-15

"""

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vcs_installation",
        sa.Column("refresh_token", sa.String(2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vcs_installation", "refresh_token")
