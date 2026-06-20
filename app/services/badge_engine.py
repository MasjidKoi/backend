"""BadgeEngine — tiered private milestone badges (PRD 08, gap #19).

Pure, deterministic, idempotent: ``evaluate(counters, already_awarded) ->
list[Award]``. Tier thresholds, no-skipped-tiers, and the Generous Giver
criterion live here and only here; the service supplies counters and
persists the returned awards. The committed tests in
``tests/test_badge_engine.py`` are the spec.

Three families, each on resonant milestone numbers:

- **Fajr Warrior** — 7 / 40 / 100 consecutive logged-Fajr days (40 carries
  religious weight).
- **Generous Giver** — 3 / 6 / 12 consecutive months containing at least one
  donation of any amount (**consistency, never amount**). **Active** since the
  PRD 05 donation system landed: donation completion feeds the consecutive-
  giving-months counter and re-evaluates this family.
- **Community Pillar** — accumulated verified-contribution points (check-ins,
  accepted info reports, approved community photos). The point weights are an
  implementer choice and live with the service that builds the counter; the
  tier thresholds live here.

Idempotency and no-skipped-tiers fall out of evaluating each tier
independently against ``already_awarded``: reaching tier 3 awards 1 and 2 too
if they were somehow never recorded, and an already-held tier is never
re-emitted.
"""

from dataclasses import dataclass

from app.models.enums import BadgeType

# Per-family ascending tier thresholds. Index + 1 is the tier number.
BADGE_THRESHOLDS: dict[BadgeType, list[int]] = {
    BadgeType.FAJR_WARRIOR: [7, 40, 100],
    BadgeType.GENEROUS_GIVER: [3, 6, 12],
    BadgeType.COMMUNITY_PILLAR: [10, 50, 150],
}


@dataclass(frozen=True, slots=True)
class BadgeCounters:
    """The progress signals each family is awarded against."""

    consecutive_fajr_days: int = 0
    consecutive_giving_months: int = 0  # fed by donation completion (PRD 05)
    contribution_points: int = 0


@dataclass(frozen=True, slots=True)
class Award:
    badge_type: str  # a BadgeType value
    tier: int


def counter_for(family: BadgeType, counters: BadgeCounters) -> int:
    """The progress value a badge family is measured against. Shared with the
    badge-gallery read path so the family→counter mapping lives in one place."""
    if family is BadgeType.FAJR_WARRIOR:
        return counters.consecutive_fajr_days
    if family is BadgeType.GENEROUS_GIVER:
        return counters.consecutive_giving_months
    return counters.contribution_points


def evaluate(
    counters: BadgeCounters, already_awarded: set[tuple[str, int]]
) -> list[Award]:
    """Return the tiers newly reached and not yet recorded, in stable order."""

    awards: list[Award] = []
    for family, thresholds in BADGE_THRESHOLDS.items():
        value = counter_for(family, counters)
        for index, threshold in enumerate(thresholds):
            tier = index + 1
            if value >= threshold and (family.value, tier) not in already_awarded:
                awards.append(Award(badge_type=family.value, tier=tier))
    return awards
