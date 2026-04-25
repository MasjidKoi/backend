"""add_scheduled_at_to_announcements

Revision ID: 9c1870440d8f
Revises: f0da157c7611
Create Date: 2026-04-26 00:34:11.848117

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c1870440d8f'
down_revision: Union[str, Sequence[str], None] = 'f0da157c7611'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('announcements', sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('announcements', 'scheduled_at')
