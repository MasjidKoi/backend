"""add_masjid_questions

Creates the masjid_questions table backing the Ask-the-masjid Q&A subsystem
(Gap #9 / PRD 04). A question is born 'pending' and travels
pending -> answered | rejected; only answered rows ever surface publicly.
asker_user_id / answered_by carry no FK (users live in GoTrue's auth schema);
answer_author_role is stored so community answers can open later without a
schema change.

Revision ID: bb2f2bad9966
Revises: b7c2a9f4e1d3
Create Date: 2026-06-16 01:56:25.148157

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bb2f2bad9966'
down_revision: Union[str, Sequence[str], None] = 'b7c2a9f4e1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOTE: autogenerate also proposed dropping idx_masjids_location (GIST),
    # idx_masjids_status_created, ix_announcements_masjid_published and adding
    # uq_user_masjid_follow — all pre-existing model<->DB drift unrelated to this
    # change (see prior migrations). Stripped intentionally; this migration only
    # adds masjid_questions.
    op.create_table('masjid_questions',
    sa.Column('question_id', sa.UUID(), nullable=False),
    sa.Column('masjid_id', sa.UUID(), nullable=False),
    sa.Column('asker_user_id', sa.UUID(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
    sa.Column('answer', sa.Text(), nullable=True),
    sa.Column('answered_by', sa.UUID(), nullable=True),
    sa.Column('answer_author_role', sa.String(length=20), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending','answered','rejected')", name='ck_masjid_questions_status'),
    sa.ForeignKeyConstraint(['masjid_id'], ['masjids.masjid_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('question_id')
    )
    op.create_index('ix_masjid_questions_asker', 'masjid_questions', ['asker_user_id'], unique=False)
    op.create_index('ix_masjid_questions_masjid_status', 'masjid_questions', ['masjid_id', 'status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_masjid_questions_masjid_status', table_name='masjid_questions')
    op.drop_index('ix_masjid_questions_asker', table_name='masjid_questions')
    op.drop_table('masjid_questions')
