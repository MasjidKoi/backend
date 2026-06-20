"""BadgeEngine tests (PRD 08, committed target).

Pure-logic — no DB. Pins tier thresholds for all three families, idempotent
awarding, lower-tiers-backfilled-not-skipped, consecutive-giving-months across
boundaries (via the counter), and the dormant Generous Giver awarding nothing.
"""

from app.models.enums import BadgeType
from app.services.badge_engine import Award, BadgeCounters, evaluate

FW = BadgeType.FAJR_WARRIOR.value
GG = BadgeType.GENEROUS_GIVER.value
CP = BadgeType.COMMUNITY_PILLAR.value


def test_fajr_warrior_tier_thresholds():
    assert evaluate(BadgeCounters(consecutive_fajr_days=6), set()) == []
    assert evaluate(BadgeCounters(consecutive_fajr_days=7), set()) == [Award(FW, 1)]
    assert evaluate(BadgeCounters(consecutive_fajr_days=40), set()) == [
        Award(FW, 1),
        Award(FW, 2),
    ]
    assert evaluate(BadgeCounters(consecutive_fajr_days=100), set()) == [
        Award(FW, 1),
        Award(FW, 2),
        Award(FW, 3),
    ]


def test_community_pillar_tier_thresholds():
    assert evaluate(BadgeCounters(contribution_points=50), set()) == [
        Award(CP, 1),
        Award(CP, 2),
    ]


def test_idempotent_does_not_reaward_held_tiers():
    held = {(FW, 1), (FW, 2)}
    # At 40 days tiers 1 and 2 are held — only nothing new until 100.
    assert evaluate(BadgeCounters(consecutive_fajr_days=40), held) == []
    assert evaluate(BadgeCounters(consecutive_fajr_days=100), held) == [Award(FW, 3)]


def test_lower_tiers_backfilled_not_skipped():
    # Reached tier 3 with only tier 2 on record → tier 1 is backfilled.
    held = {(FW, 2)}
    assert evaluate(BadgeCounters(consecutive_fajr_days=100), held) == [
        Award(FW, 1),
        Award(FW, 3),
    ]


def test_consecutive_giving_months_counter_drives_generous_giver():
    # The engine trusts the counter the service computes across month boundaries.
    assert evaluate(BadgeCounters(consecutive_giving_months=3), set()) == [Award(GG, 1)]
    assert evaluate(BadgeCounters(consecutive_giving_months=12), set()) == [
        Award(GG, 1),
        Award(GG, 2),
        Award(GG, 3),
    ]


def test_generous_giver_dormant_awards_nothing():
    # Until donations land the counter is 0 — no Generous Giver tier is reached.
    awards = evaluate(
        BadgeCounters(consecutive_fajr_days=100, contribution_points=150), set()
    )
    assert all(a.badge_type != GG for a in awards)


def test_empty_counters_award_nothing():
    assert evaluate(BadgeCounters(), set()) == []
