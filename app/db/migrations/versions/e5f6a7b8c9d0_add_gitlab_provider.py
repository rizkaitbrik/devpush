"""Add gitlab to identity_provider enum.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-13

"""

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE identity_provider ADD VALUE IF NOT EXISTS 'gitlab'")


def downgrade() -> None:
    pass
