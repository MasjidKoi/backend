"""add_hijri_offset_days

PRD 03 — global Hijri-date display correction on platform_settings, validated
to the −2…+2 range. Exposed via the public /app-config endpoint; a change
broadcasts a HIJRI_OFFSET push.

Revision ID: 38e6ec65470b
Revises: 2fb604ddb042
Create Date: 2026-06-20 22:54:57.124063

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "38e6ec65470b"
down_revision: Union[str, Sequence[str], None] = "2fb604ddb042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "platform_settings",
        sa.Column(
            "hijri_offset_days",
            sa.SmallInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_platform_settings_hijri_offset",
        "platform_settings",
        "hijri_offset_days BETWEEN -2 AND 2",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_platform_settings_hijri_offset",
        "platform_settings",
        type_="check",
    )
    op.drop_column("platform_settings", "hijri_offset_days")
