"""add_push_receipts

PRD 03 #0 follow-up — async Expo getReceipts dead-token reaping.

`push_receipts` records ``(receipt_id, token)`` for accepted sends so a
scheduled job can poll Expo's delivery receipts ~15 min later and prune tokens
reported DeviceNotRegistered (failures that don't appear in the synchronous send
ticket). Rows are transient — deleted once their receipt is checked.

Revision ID: 6d73ff6527ac
Revises: 52fcb6905428
Create Date: 2026-06-21 00:34:42.818672

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d73ff6527ac"
down_revision: Union[str, Sequence[str], None] = "52fcb6905428"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "push_receipts",
        sa.Column("receipt_id", sa.String(length=256), nullable=False),
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("receipt_id"),
    )
    op.create_index(
        "ix_push_receipts_created", "push_receipts", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_push_receipts_created", table_name="push_receipts")
    op.drop_table("push_receipts")
