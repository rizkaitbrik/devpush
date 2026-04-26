"""Move project env vars from encrypted blob to env_var table.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "env_var",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("environment", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "key", "environment", name="uq_env_var_project_key_env"
        ),
    )
    op.create_index("ix_env_var_project_id", "env_var", ["project_id"])
    op.drop_column("project", "env_vars")


def downgrade() -> None:
    op.add_column(
        "project",
        sa.Column("env_vars", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_index("ix_env_var_project_id", table_name="env_var")
    op.drop_table("env_var")
