"""Add password provider to identity_provider enum.

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-05-13

"""

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE identity_provider ADD VALUE IF NOT EXISTS 'password'")


def downgrade() -> None:
    # Postgres does not support removing enum values; downgrade is a no-op
    pass
