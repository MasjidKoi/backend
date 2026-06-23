"""add_event_end_time

Revision ID: d1e2f3a4b5c6
Revises: c8f1a2b3d4e5
Create Date: 2026-06-22 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c8f1a2b3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Optional end time for events so the detail screen can show a start–end
    range (design 81/82). Nullable — existing rows keep a single start time."""
    op.add_column(
        'masjid_events',
        sa.Column('event_end_time', sa.Time(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('masjid_events', 'event_end_time')
