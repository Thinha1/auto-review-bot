"""add GitHub check deliveries

Revision ID: 9b8f2c3d4e5f
Revises: 4271f960e24b
Create Date: 2026-09-10 20:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b8f2c3d4e5f"
down_revision: str | None = "4271f960e24b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_check_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_run_id", sa.Integer(), nullable=False),
        sa.Column("github_check_run_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("conclusion", sa.String(length=30), nullable=True),
        sa.Column("details_url", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=100), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_run_id"], ["review_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_github_check_deliveries_review_run_id",
        "github_check_deliveries",
        ["review_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_check_deliveries_review_run_id",
        table_name="github_check_deliveries",
    )
    op.drop_table("github_check_deliveries")
