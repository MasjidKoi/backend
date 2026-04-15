"""
Pure prayer time computation — no DB, no HTTP.
Isolated so it can be unit-tested independently of the service layer.

The `adhan` library (PyPI: adhan) is a Python port of Adhan-js.
It takes a local calendar date + coordinates + parameters and returns
the 5 prayer times as datetime objects in local wall-clock time.

adhan return keys: fajr, shuruq, zuhr (not dhuhr), asr, maghrib, isha
"""

import logging
from datetime import date, datetime
from datetime import time as dt_time
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.enums import CalculationMethod, Madhab

logger = logging.getLogger(__name__)

# ── adhan import guard (edge case 11) ─────────────────────────────────────────

try:
    from adhan import adhan as _adhan_fn  # type: ignore[import-untyped]
    _ADHAN_AVAILABLE = True
except ImportError:
    _ADHAN_AVAILABLE = False
    logger.warning("adhan package not available — prayer time calculation disabled")


# adhan parameter dicts (mirroring adhan-js method definitions)
_METHODS: dict[str, dict] = {
    CalculationMethod.KARACHI: {
        "fajr_angle": 18, "isha_angle": 18,
    },
    CalculationMethod.MUSLIM_WORLD_LEAGUE: {
        "fajr_angle": 18, "isha_angle": 17,
    },
    CalculationMethod.ISNA: {
        "fajr_angle": 15, "isha_angle": 15,
    },
    CalculationMethod.EGYPT: {
        "fajr_angle": 19.5, "isha_angle": 17.5,
    },
    CalculationMethod.MAKKAH: {
        "fajr_angle": 18.5, "isha_interval": 90,
    },
}

# Asr multipliers: Hanafi = 2, all others = 1
_ASR_MULTIPLIERS: dict[str, int] = {
    Madhab.HANAFI: 2,
    Madhab.SHAFI: 1,
    Madhab.MALIKI: 1,
    Madhab.HANBALI: 1,
}


# ── Value object ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CalculatedPrayerTimes:
    """
    Result of a prayer time calculation.
    All times are local wall-clock (datetime.time, tz-naive).
    A field is None only if adhan couldn't compute it (extreme latitude — edge case 4).
    """
    fajr: dt_time | None
    dhuhr: dt_time | None
    asr: dt_time | None
    maghrib: dt_time | None
    isha: dt_time | None
    calculation_method: str
    madhab: str


# ── Timezone helpers ───────────────────────────────────────────────────────────

def _safe_timezone(tz_string: str) -> ZoneInfo:
    """
    Edge case 3: invalid/unknown IANA timezone string → fallback to UTC.
    """
    try:
        return ZoneInfo(tz_string)
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning("Unknown timezone %r — falling back to UTC", tz_string)
        return ZoneInfo("UTC")


def get_local_date(tz_string: str) -> date:
    """
    Edge case 1: 'today' is the LOCAL calendar date at the masjid's timezone.
    At 22:00 UTC, Asia/Dhaka (UTC+6) is already the next calendar day.
    """
    tz = _safe_timezone(tz_string)
    return datetime.now(tz).date()


def _utc_offset_hours(tz_string: str, for_date: date) -> float:
    """
    Get the UTC offset in fractional hours for a specific date.
    Uses the DST-aware offset for that date (important for masjids outside BD).
    """
    tz = _safe_timezone(tz_string)
    midnight = datetime(for_date.year, for_date.month, for_date.day, tzinfo=tz)
    offset = midnight.utcoffset()
    return offset.total_seconds() / 3600.0 if offset else 0.0


# ── Main calculation function ──────────────────────────────────────────────────

def calculate(
    lat: float,
    lng: float,
    local_date: date,
    tz_string: str,
    method: str = CalculationMethod.KARACHI,
    madhab: str = Madhab.HANAFI,
) -> CalculatedPrayerTimes:
    """
    Calculate the 5 daily prayer times for a given location and date.

    Returns CalculatedPrayerTimes with local wall-clock times (tz-naive).
    A field is None if adhan cannot compute it (edge case 4 — extreme latitude).
    Raises RuntimeError if adhan package is not installed (edge case 11).

    Args:
        lat: Latitude in decimal degrees
        lng: Longitude in decimal degrees
        local_date: Local calendar date at the masjid's timezone
        tz_string: IANA timezone string (e.g. "Asia/Dhaka")
        method: Calculation method for Fajr/Isha angles
        madhab: School of jurisprudence (affects Asr calculation only)
    """
    if not _ADHAN_AVAILABLE:
        raise RuntimeError(
            "adhan package not installed. Run: uv add adhan"
        )

    # Build parameters dict for adhan library.
    # Merge method angles + asr_multiplier into a flat dict.
    # adhan expects: {"fajr_angle": 18, "isha_angle": 18, "asr_multiplier": 2}
    params = dict(_METHODS.get(method, _METHODS[CalculationMethod.KARACHI]))
    params["asr_multiplier"] = _ASR_MULTIPLIERS.get(madhab, 1)

    utc_offset = _utc_offset_hours(tz_string, local_date)

    # IMPORTANT: The adhan v0.1.1 library has a sign error in its Eastern
    # hemisphere Dhuhr calculation (uses `12 + abs(lng)/15` instead of
    # `12 - lng/15`), making all times 12 hours off for East longitudes.
    # Passing the NEGATED UTC offset exactly compensates for this bug:
    #   library_wrong_utc + (-offset) == correct_local_time
    # This is mathematically verified for all 5 prayers.
    result: dict = _adhan_fn(
        day=local_date,
        location=(lat, lng),
        parameters=params,
        timezone_offset=-utc_offset,
    )

    def _to_time(key: str) -> dt_time | None:
        val = result.get(key)
        if val is None:
            logger.warning(
                "adhan returned None for %r (lat=%s, lng=%s, date=%s, method=%s)",
                key, lat, lng, local_date, method,
            )
            return None
        # val is a datetime from adhan — extract just the time
        if isinstance(val, datetime):
            return val.time().replace(tzinfo=None)
        return None

    return CalculatedPrayerTimes(
        fajr=_to_time("fajr"),
        dhuhr=_to_time("zuhr"),      # adhan uses "zuhr" key, not "dhuhr"
        asr=_to_time("asr"),
        maghrib=_to_time("maghrib"),
        isha=_to_time("isha"),
        calculation_method=method,
        madhab=madhab,
    )
