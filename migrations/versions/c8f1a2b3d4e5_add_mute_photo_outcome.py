"""add_mute_photo_outcome

Revision ID: c8f1a2b3d4e5
Revises: b7c3ec7d31a7
Create Date: 2026-06-22 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8f1a2b3d4e5'
down_revision: Union[str, Sequence[str], None] = 'b7c3ec7d31a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Split photo-approval outcomes onto their own push opt-out (PRD 09 #28).

    Previously PHOTO_APPROVED shared mute_moderation_outcome with submission/QnA
    outcomes; the settings UI now exposes it as a separate "Photo approval
    updates" switch. server_default false backfills existing rows so the NOT NULL
    is satisfied atomically (matches the unmuted default of the sibling columns).
    """
    op.add_column(
        'user_profiles',
        sa.Column(
            'mute_photo_outcome',
            sa.Boolean(),
            server_default=sa.text('false'),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('user_profiles', 'mute_photo_outcome')
