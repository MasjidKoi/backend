"""add_community_photo_fields

Adds the community-submission lifecycle to masjid_photos: source
('admin' | 'community'), status ('pending' | 'approved' | 'rejected'),
uploaded_by (nullable, no FK — users live in GoTrue), and updated_at.

Existing rows backfill as admin/approved via the column server defaults, which
matches their meaning (the pre-existing rows are all the curated admin gallery).

Revision ID: b7c2a9f4e1d3
Revises: e4dcb262fe95
Create Date: 2026-06-16 02:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c2a9f4e1d3'
down_revision: Union[str, Sequence[str], None] = 'e4dcb262fe95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default backfills every pre-existing row as admin/approved — these
    # are all the curated gallery, so the default is the correct historical value.
    op.add_column(
        'masjid_photos',
        sa.Column('source', sa.String(length=20), server_default='admin', nullable=False),
    )
    op.add_column(
        'masjid_photos',
        sa.Column('status', sa.String(length=20), server_default='approved', nullable=False),
    )
    op.add_column(
        'masjid_photos',
        sa.Column('uploaded_by', sa.UUID(), nullable=True),
    )
    op.add_column(
        'masjid_photos',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        'ck_masjid_photos_source',
        'masjid_photos',
        "source IN ('admin','community')",
    )
    op.create_check_constraint(
        'ck_masjid_photos_status',
        'masjid_photos',
        "status IN ('pending','approved','rejected')",
    )
    op.create_index(
        'ix_masjid_photos_masjid_source_status',
        'masjid_photos',
        ['masjid_id', 'source', 'status'],
        unique=False,
    )
    op.create_index(
        op.f('ix_masjid_photos_uploaded_by'),
        'masjid_photos',
        ['uploaded_by'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_masjid_photos_uploaded_by'), table_name='masjid_photos')
    op.drop_index('ix_masjid_photos_masjid_source_status', table_name='masjid_photos')
    op.drop_constraint('ck_masjid_photos_status', 'masjid_photos', type_='check')
    op.drop_constraint('ck_masjid_photos_source', 'masjid_photos', type_='check')
    op.drop_column('masjid_photos', 'updated_at')
    op.drop_column('masjid_photos', 'uploaded_by')
    op.drop_column('masjid_photos', 'status')
    op.drop_column('masjid_photos', 'source')
