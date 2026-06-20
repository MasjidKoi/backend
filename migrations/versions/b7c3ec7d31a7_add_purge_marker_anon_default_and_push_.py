"""add_purge_marker_anon_default_and_push_mutes

Revision ID: b7c3ec7d31a7
Revises: cf15d14cf9cc
Create Date: 2026-06-21 02:23:51.471768

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c3ec7d31a7'
down_revision: Union[str, Sequence[str], None] = 'cf15d14cf9cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Six columns on user_profiles: the donate-anonymously-by-default preference
    (PRD 05 #3), four per-message-type push opt-outs (PRD 05 #4 / PRD 09 #28),
    and the purge idempotency marker (PRD 09 #1). server_default false backfills
    existing rows so the NOT NULL is satisfied atomically.

    NOTE: the autogenerate also proposed dropping idx_masjids_location (GIST),
    idx_masjids_status_created, ix_announcements_masjid_published and adding a
    uq_user_masjid_follow constraint — all pre-existing drift between the hand-
    written DDL of earlier migrations and the model metadata, NOT part of this
    change. Deliberately omitted (dropping the spatial index would break PRD 02
    nearby/search).
    """
    op.add_column('user_profiles', sa.Column('donate_anonymously_by_default', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_profiles', sa.Column('mute_donation_nudge', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_profiles', sa.Column('mute_campaign_milestone', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_profiles', sa.Column('mute_moderation_outcome', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_profiles', sa.Column('mute_promotions', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('user_profiles', sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_profiles', 'purged_at')
    op.drop_column('user_profiles', 'mute_promotions')
    op.drop_column('user_profiles', 'mute_moderation_outcome')
    op.drop_column('user_profiles', 'mute_campaign_milestone')
    op.drop_column('user_profiles', 'mute_donation_nudge')
    op.drop_column('user_profiles', 'donate_anonymously_by_default')
