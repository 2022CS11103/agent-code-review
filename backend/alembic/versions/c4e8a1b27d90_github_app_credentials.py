"""github app credentials

Revision ID: c4e8a1b27d90
Revises: 79ad55460505
Create Date: 2026-10-14 10:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b27d90"
down_revision: Union[str, Sequence[str], None] = "79ad55460505"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_app_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("app_id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("private_key", sa.Text(), nullable=False),
        sa.Column("webhook_secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_github_app_credentials_app_id"), "github_app_credentials", ["app_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_github_app_credentials_app_id"), table_name="github_app_credentials")
    op.drop_table("github_app_credentials")
