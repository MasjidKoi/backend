"""rework_gamification_prd08

PRD 08 gamification rework (#17, #18, #19) — the deployed v0 measured the wrong
thing. Three coordinated schema changes, landed together as R1's first task:

- user_journal_entries: the free-text `prayers_logged` string becomes a
  structured five-prayer boolean set (fajr/dhuhr/asr/maghrib/isha); `quran_pages`
  generalises to `quran_amount` + `quran_unit`; a deliberately-ambiguous
  `is_protected` marker (freeze == exempt on the wire) is added. v0 free-text
  rows are token-parsed best-effort into the booleans, else preserved into notes.
- user_badges: gains a `tier` dimension; uniqueness moves from
  (user, badge_type) to (user, badge_type, tier). v0 rows were awarded on
  check-in criteria that no longer exist, so they are wiped.

Revision ID: edc8042ef40b
Revises: c3f7a9e21b08
Create Date: 2026-06-17 09:30:38.625396

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "edc8042ef40b"
down_revision: Union[str, Sequence[str], None] = "c3f7a9e21b08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── Journal: structured prayer booleans + quran amount/unit + protected ──
    for prayer in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        op.add_column(
            "user_journal_entries",
            sa.Column(prayer, sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    op.add_column(
        "user_journal_entries",
        sa.Column("quran_amount", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "user_journal_entries",
        sa.Column("quran_unit", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "user_journal_entries",
        sa.Column(
            "is_protected", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    # Co-enforce the unit domain the response schema requires, so an out-of-range
    # value can never be persisted and 500 the journal read.
    op.create_check_constraint(
        "ck_journal_quran_unit",
        "user_journal_entries",
        "quran_unit IS NULL OR quran_unit IN ('pages', 'juz', 'minutes')",
    )

    # Best-effort migration of v0 free-text rows. Each prayer flag is set when
    # its name appears in the old free-text; quran_pages carries over as pages.
    # Greenfield: little-to-no real data, so this is defensive, not exhaustive.
    op.execute(
        """
        UPDATE user_journal_entries SET
            fajr    = COALESCE(prayers_logged ILIKE '%fajr%', false),
            dhuhr   = COALESCE(prayers_logged ILIKE '%dhuhr%' OR prayers_logged ILIKE '%zuhr%', false),
            asr     = COALESCE(prayers_logged ILIKE '%asr%', false),
            maghrib = COALESCE(prayers_logged ILIKE '%maghrib%', false),
            isha    = COALESCE(prayers_logged ILIKE '%isha%', false)
        WHERE prayers_logged IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE user_journal_entries
        SET quran_amount = quran_pages, quran_unit = 'pages'
        WHERE quran_pages IS NOT NULL
        """
    )
    # Preserve the ORIGINAL free-text into notes before the column is dropped —
    # token-parsing is best-effort and lossy (partial matches, false positives),
    # so we keep the raw text for every migrated row, not just the unparsed ones.
    op.execute(
        """
        UPDATE user_journal_entries
        SET notes = TRIM(BOTH E'\n' FROM
            COALESCE(notes || E'\n', '') || 'migrated prayers_logged: ' || prayers_logged)
        WHERE prayers_logged IS NOT NULL
        """
    )

    op.drop_column("user_journal_entries", "prayers_logged")
    op.drop_column("user_journal_entries", "quran_pages")

    # ── Badges: add tier dimension; wipe meaningless v0 (check-in) rows ──────
    op.execute("DELETE FROM user_badges")
    op.drop_constraint("uq_user_badge_type", "user_badges", type_="unique")
    # server_default makes the NOT NULL add self-safe even if rows survive a
    # partial run; drop it after so tier is always set explicitly by the app.
    op.add_column(
        "user_badges",
        sa.Column("tier", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.alter_column("user_badges", "tier", server_default=None)
    op.create_unique_constraint(
        "uq_user_badge_type_tier", "user_badges", ["user_id", "badge_type", "tier"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Badges
    op.drop_constraint("uq_user_badge_type_tier", "user_badges", type_="unique")
    op.drop_column("user_badges", "tier")
    op.create_unique_constraint(
        "uq_user_badge_type", "user_badges", ["user_id", "badge_type"]
    )

    # Journal
    op.drop_constraint("ck_journal_quran_unit", "user_journal_entries", type_="check")
    op.add_column(
        "user_journal_entries",
        sa.Column("quran_pages", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "user_journal_entries",
        sa.Column("prayers_logged", sa.Text(), nullable=True),
    )
    op.execute(
        """
        UPDATE user_journal_entries
        SET quran_pages = quran_amount
        WHERE quran_unit = 'pages' AND quran_amount IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE user_journal_entries SET prayers_logged = TRIM(
            CONCAT_WS(',',
                CASE WHEN fajr THEN 'fajr' END,
                CASE WHEN dhuhr THEN 'dhuhr' END,
                CASE WHEN asr THEN 'asr' END,
                CASE WHEN maghrib THEN 'maghrib' END,
                CASE WHEN isha THEN 'isha' END))
        WHERE fajr OR dhuhr OR asr OR maghrib OR isha
        """
    )
    op.drop_column("user_journal_entries", "is_protected")
    op.drop_column("user_journal_entries", "quran_unit")
    op.drop_column("user_journal_entries", "quran_amount")
    op.drop_column("user_journal_entries", "isha")
    op.drop_column("user_journal_entries", "maghrib")
    op.drop_column("user_journal_entries", "asr")
    op.drop_column("user_journal_entries", "dhuhr")
    op.drop_column("user_journal_entries", "fajr")
