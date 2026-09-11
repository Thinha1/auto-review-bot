"""add repository usage budgets

Revision ID: f1a2b3c4d5e6
Revises: 9b8f2c3d4e5f
Create Date: 2026-09-10 20:45:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "9b8f2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_output_tokens_per_call",
                sa.Integer(),
                server_default="4000",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "monthly_token_budget",
                sa.Integer(),
                server_default="1000000",
                nullable=False,
            )
        )

    with op.batch_alter_table("review_runs") as batch_op:
        batch_op.add_column(sa.Column("model_calls", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("usage_period_start", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "usage_reservation_tokens",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("usage_settled_at", sa.DateTime(timezone=True), nullable=True)
        )

    op.create_table(
        "repository_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("review_runs", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "repository_id",
            "period_start",
            name="uq_repository_usage_period",
        ),
    )
    op.create_index(
        "ix_repository_usage_repository_id",
        "repository_usage",
        ["repository_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_repository_usage_repository_id", table_name="repository_usage")
    op.drop_table("repository_usage")

    with op.batch_alter_table("review_runs") as batch_op:
        batch_op.drop_column("usage_settled_at")
        batch_op.drop_column("usage_reservation_tokens")
        batch_op.drop_column("usage_period_start")
        batch_op.drop_column("model_calls")

    with op.batch_alter_table("review_configs") as batch_op:
        batch_op.drop_column("monthly_token_budget")
        batch_op.drop_column("max_output_tokens_per_call")
