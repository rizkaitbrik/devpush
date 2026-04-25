"""Make VCS installation schema provider-agnostic.

Revision ID: a1b2c3d4e5f6
Revises: f45484bf96b0
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f45484bf96b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("github_installation", "vcs_installation")

    op.add_column(
        "vcs_installation",
        sa.Column("id", sa.String(32), nullable=True),
    )
    op.execute(
        "UPDATE vcs_installation SET id = substr(md5(installation_id::text || '-github'), 1, 32)"
    )
    op.alter_column("vcs_installation", "id", nullable=False)

    op.execute("ALTER TABLE vcs_installation DROP CONSTRAINT github_installation_pkey")
    op.create_primary_key("vcs_installation_pkey", "vcs_installation", ["id"])

    op.add_column(
        "vcs_installation",
        sa.Column(
            "provider",
            sa.String(50),
            nullable=False,
            server_default="github",
        ),
    )

    op.add_column(
        "vcs_installation",
        sa.Column("provider_account_id", sa.String(100), nullable=True),
    )
    op.execute("UPDATE vcs_installation SET provider_account_id = installation_id::text")

    op.add_column(
        "vcs_installation",
        sa.Column("provider_account_name", sa.String(255), nullable=True),
    )

    op.execute("ALTER TYPE github_installation_status RENAME TO vcs_installation_status")
    op.execute("ALTER TYPE project_github_status RENAME TO project_vcs_status")

    op.add_column(
        "project",
        sa.Column("vcs_installation_id", sa.String(32), nullable=True),
    )
    op.execute(
        """
        UPDATE project p
        SET vcs_installation_id = v.id
        FROM vcs_installation v
        WHERE p.github_installation_id = v.installation_id
        """
    )
    op.alter_column("project", "vcs_installation_id", nullable=False)

    op.drop_constraint("project_github_installation_id_fkey", "project", type_="foreignkey")
    op.drop_index("ix_project_github_installation_id", table_name="project")
    op.drop_column("project", "github_installation_id")

    op.create_index("ix_project_vcs_installation_id", "project", ["vcs_installation_id"])
    op.create_foreign_key(
        "fk_project_vcs_installation_id",
        "project",
        "vcs_installation",
        ["vcs_installation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column("vcs_installation", "provider", server_default=None)


def downgrade() -> None:
    op.add_column(
        "project",
        sa.Column("github_installation_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE project p
        SET github_installation_id = v.installation_id
        FROM vcs_installation v
        WHERE p.vcs_installation_id = v.id
        """
    )
    op.alter_column("project", "github_installation_id", nullable=False)

    op.drop_constraint("fk_project_vcs_installation_id", "project", type_="foreignkey")
    op.drop_index("ix_project_vcs_installation_id", table_name="project")
    op.drop_column("project", "vcs_installation_id")

    op.create_index("ix_project_github_installation_id", "project", ["github_installation_id"])
    op.create_foreign_key(
        "project_github_installation_id_fkey",
        "project",
        "vcs_installation",
        ["github_installation_id"],
        ["installation_id"],
    )

    op.execute("ALTER TYPE project_vcs_status RENAME TO project_github_status")
    op.execute("ALTER TYPE vcs_installation_status RENAME TO github_installation_status")

    op.drop_column("vcs_installation", "provider_account_name")
    op.drop_column("vcs_installation", "provider_account_id")
    op.drop_column("vcs_installation", "provider")

    op.execute("ALTER TABLE vcs_installation DROP CONSTRAINT vcs_installation_pkey")
    op.create_primary_key("github_installation_pkey", "vcs_installation", ["installation_id"])
    op.drop_column("vcs_installation", "id")

    op.rename_table("vcs_installation", "github_installation")
