"""add_prd08_goals_and_report_attribution

PRD 08 §Goals + Community Pillar:

- ``user_goals`` — template-led/free-form ibadah goals. ``quran_quantity`` goals
  carry a target/unit/window (journal-fed progress); ``recurring`` goals carry a
  cadence (daily/weekly check-off). Domains fenced by CHECKs.
- ``goal_completions`` — one idempotent check-off per (goal, date) for recurring
  goals; cascades on goal delete.
- ``masjid_reports.user_id`` — attributes a report to its submitter so an
  accepted (resolved) report can count toward Community Pillar. Nullable (guest
  reports stay allowed); bare UUID with an index, no FK (journal/badge convention).

Revision ID: 52fcb6905428
Revises: 38e6ec65470b
Create Date: 2026-06-21 00:02:05.981593

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "52fcb6905428"
down_revision: Union[str, Sequence[str], None] = "38e6ec65470b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_goals",
        sa.Column("goal_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("goal_kind", sa.String(length=20), nullable=False),
        sa.Column("template", sa.String(length=30), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="active", nullable=False
        ),
        sa.Column("target_amount", sa.SmallInteger(), nullable=True),
        sa.Column("unit", sa.String(length=10), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("recurrence", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "goal_kind IN ('quran_quantity', 'recurring')", name="ck_goals_kind"
        ),
        sa.CheckConstraint(
            "recurrence IS NULL OR recurrence IN ('daily', 'weekly')",
            name="ck_goals_recurrence",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'abandoned')", name="ck_goals_status"
        ),
        sa.CheckConstraint(
            "unit IS NULL OR unit IN ('pages', 'juz', 'minutes')", name="ck_goals_unit"
        ),
        sa.PrimaryKeyConstraint("goal_id"),
    )
    op.create_index(
        "idx_goals_user_status", "user_goals", ["user_id", "status"], unique=False
    )
    op.create_table(
        "goal_completions",
        sa.Column("completion_id", sa.UUID(), nullable=False),
        sa.Column("goal_id", sa.UUID(), nullable=False),
        sa.Column("completion_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"], ["user_goals.goal_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("completion_id"),
        sa.UniqueConstraint(
            "goal_id", "completion_date", name="uq_goal_completion_date"
        ),
    )
    op.create_index(
        "idx_goal_completions_goal", "goal_completions", ["goal_id"], unique=False
    )
    op.add_column("masjid_reports", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_index(
        op.f("ix_masjid_reports_user_id"), "masjid_reports", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_masjid_reports_user_id"), table_name="masjid_reports")
    op.drop_column("masjid_reports", "user_id")
    op.drop_index("idx_goal_completions_goal", table_name="goal_completions")
    op.drop_table("goal_completions")
    op.drop_index("idx_goals_user_status", table_name="user_goals")
    op.drop_table("user_goals")
