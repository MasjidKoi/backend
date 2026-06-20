"""add_prd07_community_feed

PRD 07 (Community Features) backend schema, shipped as one migration since the
slices share a release:

- device_tokens: the push token registry (PRD 03's subsystem, minimal core)
  backing instant + digest fan-out. token is the unique idempotency key.
- user_masjid_follows.notification_mode: per-follow announcement mode
  ('digest' | 'instant' | 'mute'); existing rows backfill to 'digest'.
- user_profiles.digest_hour (0–23, default 19, Asia/Dhaka) +
  last_digest_sent_at: per-user daily-digest delivery hour and the idempotency
  timestamp the hourly digest job stamps.
- masjid_reviews.edited + updated_at: the upsert stamps `edited` on replacement.

Revision ID: c3f7a9e21b08
Revises: bb2f2bad9966
Create Date: 2026-06-16 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f7a9e21b08'
down_revision: Union[str, Sequence[str], None] = 'bb2f2bad9966'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── Push token registry ─────────────────────────────────────────────────
    op.create_table(
        'device_tokens',
        sa.Column('device_token_id', sa.UUID(), nullable=False),
        sa.Column('token', sa.String(length=512), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('platform', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("platform IN ('ios','android','web')", name='ck_device_tokens_platform'),
        sa.PrimaryKeyConstraint('device_token_id'),
        sa.UniqueConstraint('token', name='uq_device_tokens_token'),
    )
    op.create_index('ix_device_tokens_user', 'device_tokens', ['user_id'], unique=False)

    # ── Per-follow notification mode ────────────────────────────────────────
    # server_default backfills every existing follow as 'digest' — the spec'd
    # default — so no follower silently loses notifications.
    op.add_column(
        'user_masjid_follows',
        sa.Column('notification_mode', sa.String(length=20), server_default='digest', nullable=False),
    )
    op.create_check_constraint(
        'ck_user_masjid_follow_mode',
        'user_masjid_follows',
        "notification_mode IN ('digest','instant','mute')",
    )

    # ── Per-user digest hour + idempotency timestamp ────────────────────────
    op.add_column(
        'user_profiles',
        sa.Column('digest_hour', sa.Integer(), server_default='19', nullable=False),
    )
    op.add_column(
        'user_profiles',
        sa.Column('last_digest_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        'ck_user_profiles_digest_hour',
        'user_profiles',
        'digest_hour BETWEEN 0 AND 23',
    )

    # ── Review edited marker + updated_at ───────────────────────────────────
    op.add_column(
        'masjid_reviews',
        sa.Column('edited', sa.Boolean(), server_default='false', nullable=False),
    )
    op.add_column(
        'masjid_reviews',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('masjid_reviews', 'updated_at')
    op.drop_column('masjid_reviews', 'edited')
    op.drop_constraint('ck_user_profiles_digest_hour', 'user_profiles', type_='check')
    op.drop_column('user_profiles', 'last_digest_sent_at')
    op.drop_column('user_profiles', 'digest_hour')
    op.drop_constraint('ck_user_masjid_follow_mode', 'user_masjid_follows', type_='check')
    op.drop_column('user_masjid_follows', 'notification_mode')
    op.drop_index('ix_device_tokens_user', table_name='device_tokens')
    op.drop_table('device_tokens')
