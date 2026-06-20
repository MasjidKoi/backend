"""add support_ticket donation_id + DonationIssue category

Revision ID: cf15d14cf9cc
Revises: 6d73ff6527ac
Create Date: 2026-06-21 01:32:13.557408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf15d14cf9cc'
down_revision: Union[str, Sequence[str], None] = '6d73ff6527ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    NOTE: autogenerate also emitted drops for GIST/partial indexes it cannot
    introspect (idx_masjids_location, idx_masjids_status_created,
    ix_announcements_masjid_published) and a re-create of an already-present
    unique constraint (uq_user_masjid_follow). Those are false positives and
    have been removed — this migration only touches support_tickets.
    """
    # PRD 05 US 51 — optional reference to the donation a ticket was opened from.
    op.add_column(
        "support_tickets",
        sa.Column("donation_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_support_tickets_donation_id",
        "support_tickets",
        "donations",
        ["donation_id"],
        ["donation_id"],
        ondelete="SET NULL",
    )
    # Widen the category CHECK to admit the new 'DonationIssue' value.
    op.drop_constraint(
        "ck_support_tickets_category", "support_tickets", type_="check"
    )
    op.create_check_constraint(
        "ck_support_tickets_category",
        "support_tickets",
        "category IN ('Bug','IncorrectData','FeatureRequest','DonationIssue','Other')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_support_tickets_category", "support_tickets", type_="check"
    )
    op.create_check_constraint(
        "ck_support_tickets_category",
        "support_tickets",
        "category IN ('Bug','IncorrectData','FeatureRequest','Other')",
    )
    op.drop_constraint(
        "fk_support_tickets_donation_id", "support_tickets", type_="foreignkey"
    )
    op.drop_column("support_tickets", "donation_id")
