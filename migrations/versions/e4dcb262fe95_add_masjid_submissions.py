"""add_masjid_submissions

Revision ID: e4dcb262fe95
Revises: 9c1870440d8f
Create Date: 2026-06-16 01:27:05.678173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4dcb262fe95'
down_revision: Union[str, Sequence[str], None] = '9c1870440d8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: autogenerate also proposed dropping idx_masjids_location (GIST),
    # idx_masjids_status_created, ix_announcements_masjid_published and adding
    # uq_user_masjid_follow — all pre-existing model<->DB drift unrelated to this
    # change. Stripped intentionally; this migration only adds masjid_submissions.
    op.create_table('masjid_submissions',
    sa.Column('submission_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('photo_key', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('approved_masjid_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','approved','rejected')", name='ck_masjid_submissions_status'),
    sa.ForeignKeyConstraint(['approved_masjid_id'], ['masjids.masjid_id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('submission_id')
    )
    op.create_index(op.f('ix_masjid_submissions_created_at'), 'masjid_submissions', ['created_at'], unique=False)
    op.create_index(op.f('ix_masjid_submissions_user_id'), 'masjid_submissions', ['user_id'], unique=False)
    op.create_index('ix_masjid_submissions_user_status', 'masjid_submissions', ['user_id', 'status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_masjid_submissions_user_status', table_name='masjid_submissions')
    op.drop_index(op.f('ix_masjid_submissions_user_id'), table_name='masjid_submissions')
    op.drop_index(op.f('ix_masjid_submissions_created_at'), table_name='masjid_submissions')
    op.drop_table('masjid_submissions')
