"""enable_postgis

Revision ID: df147ef6544b
Revises:
Create Date: 2026-04-15 22:17:40.735667

"""
from typing import Sequence, Union

from alembic import op

revision: str = "df147ef6544b"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis")
