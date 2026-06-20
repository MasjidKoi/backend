"""PRD 01 — madhab vocabulary normalization (audit finding).

``PATCH /users/me`` used to accept/store CamelCase, two-i "Shafii" madhab strings,
while the canonical ``Madhab`` enum and the Asr calculator key on lowercase one-i
"shafi". These pure unit tests pin the normalizer to the canonical vocabulary and
guard that every value it can emit is a live key in
``prayer_calculator._ASR_MULTIPLIERS`` — so the day profile madhab is wired into Asr,
the lookup can never silently miss and fall back to the wrong multiplier.
"""

import pytest

from app.models.enums import Madhab
from app.schemas.user import _normalize_madhab
from app.services.prayer_calculator import _ASR_MULTIPLIERS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hanafi", "hanafi"),
        ("HANAFI", "hanafi"),
        ("hanafi", "hanafi"),
        ("  Hanafi  ", "hanafi"),
        ("Shafii", "shafi"),  # legacy two-i spelling -> canonical one-i
        ("shafii", "shafi"),
        ("Shafi", "shafi"),
        ("Maliki", "maliki"),
        ("Hanbali", "hanbali"),
    ],
)
def test_normalizes_to_canonical_lowercase(raw: str, expected: str) -> None:
    assert _normalize_madhab(raw) == expected


def test_none_passes_through() -> None:
    assert _normalize_madhab(None) is None


@pytest.mark.parametrize("bad", ["jafari", "", "hanfi", "ja'fari"])
def test_invalid_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        _normalize_madhab(bad)


def test_every_normalized_value_is_a_valid_asr_multiplier_key() -> None:
    """Consistency guard: the normalizer can only emit canonical Madhab values, and
    every one must resolve in the Asr multiplier table (no silent .get() miss)."""
    for member in Madhab:
        normalized = _normalize_madhab(member.value)
        assert normalized in _ASR_MULTIPLIERS
    # The legacy alias must also land on a real key.
    assert _normalize_madhab("Shafii") in _ASR_MULTIPLIERS
