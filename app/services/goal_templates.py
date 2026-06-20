"""Goal templates — the preset, one-tap goals that lead the UI (PRD 08 §Goals).

Pure data: a registry mapping each :class:`GoalTemplateKey` to the goal it
instantiates. The service reads this to build a :class:`UserGoal`; nothing else
hardcodes a template's shape. Khatm-in-Ramadan requires the caller to supply the
Ramadan window (``requires_date_range``) so the backend stays Hijri-agnostic —
the client knows Ramadan's Gregorian dates (and may apply the platform Hijri
offset). The recitation templates are open-ended check-offs.
"""

from dataclasses import dataclass

from app.models.enums import (
    GoalKind,
    GoalRecurrence,
    GoalTemplateKey,
)

# The standard mushaf is 604 pages — one full Khatm.
KHATM_TARGET_PAGES = 604


@dataclass(frozen=True, slots=True)
class GoalTemplate:
    key: str
    title: str
    goal_kind: GoalKind
    target_amount: int | None = None
    unit: str | None = None
    recurrence: GoalRecurrence | None = None
    # Khatm-in-Ramadan is bound to a window the caller supplies; the recitation
    # templates are open-ended, so dates are ignored for them.
    requires_date_range: bool = False


GOAL_TEMPLATES: dict[GoalTemplateKey, GoalTemplate] = {
    GoalTemplateKey.KHATM_RAMADAN: GoalTemplate(
        key=GoalTemplateKey.KHATM_RAMADAN.value,
        title="Khatm in Ramadan",
        goal_kind=GoalKind.QURAN_QUANTITY,
        target_amount=KHATM_TARGET_PAGES,
        unit="pages",
        requires_date_range=True,
    ),
    GoalTemplateKey.AYAT_AL_KURSI: GoalTemplate(
        key=GoalTemplateKey.AYAT_AL_KURSI.value,
        title="Daily Ayat al-Kursi",
        goal_kind=GoalKind.RECURRING,
        recurrence=GoalRecurrence.DAILY,
    ),
    GoalTemplateKey.SURAH_AL_KAHF: GoalTemplate(
        key=GoalTemplateKey.SURAH_AL_KAHF.value,
        title="Surah al-Kahf on Jumu'ah",
        goal_kind=GoalKind.RECURRING,
        recurrence=GoalRecurrence.WEEKLY,
    ),
}
