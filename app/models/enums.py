from enum import StrEnum


class AdminRole(StrEnum):
    """
    Admin roles stored in GoTrue app_metadata.role.

    These are server-assigned (service_role only) and appear as claims
    in every JWT issued by GoTrue. FastAPI reads this claim to gate
    access to protected routes.

    Hierarchy:
        PLATFORM_ADMIN  ─ full access to everything, requires TOTP (aal2)
        MASJID_ADMIN    ─ scoped to one masjid (app_metadata.masjid_id)
        MADRASHA_ADMIN  ─ scoped to one madrasha (app_metadata.madrasha_id)
    """

    PLATFORM_ADMIN = "platform_admin"
    MASJID_ADMIN = "masjid_admin"
    MADRASHA_ADMIN = "madrasha_admin"
    APP_USER = "app_user"


class AuthAssuranceLevel(StrEnum):
    """
    GoTrue Authentication Assurance Level (aal) claim in JWT.

    AAL1 = password only
    AAL2 = password + second factor (TOTP)

    Platform admins MUST have AAL2 to access sensitive endpoints.
    """

    AAL1 = "aal1"
    AAL2 = "aal2"


class MasjidStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class MasjidSubmissionStatus(StrEnum):
    """Lifecycle of a community-submitted masjid awaiting platform review."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PhotoSource(StrEnum):
    """Origin of a masjid photo.

    ADMIN photos are the curated profile gallery (uploaded by the masjid's
    admin / platform admin). COMMUNITY photos are visitor submissions that pass
    through a pending → approved | rejected moderation lifecycle before they are
    ever shown publicly.
    """

    ADMIN = "admin"
    COMMUNITY = "community"


class PhotoModerationStatus(StrEnum):
    """Moderation lifecycle of a masjid photo.

    Admin photos are born APPROVED. Community submissions are born PENDING and
    only become publicly visible once APPROVED; REJECTED photos remain visible
    to their submitter only (no public trace).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class QuestionStatus(StrEnum):
    """Moderation lifecycle of a masjid question.

    Questions are born PENDING and only become publicly visible once ANSWERED.
    REJECTED questions remain visible to their asker only (no public trace).
    """

    PENDING = "pending"
    ANSWERED = "answered"
    REJECTED = "rejected"


class AnswerAuthorRole(StrEnum):
    """Role of whoever answered a masjid question.

    Stored explicitly so community-authored answers can open later without a
    schema change — today only the masjid's own admin or a platform admin answer.
    """

    MASJID_ADMIN = "masjid_admin"
    PLATFORM_ADMIN = "platform_admin"
    COMMUNITY = "community"


class MadrashaStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class DonationStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    REFUNDED = "refunded"
    FAILED = "failed"


class DonationCategory(StrEnum):
    GENERAL = "general"
    BUILDING = "building"
    ZAKAT = "zakat"
    SADAQAH = "sadaqah"
    LILLAH = "lillah"
    CAMPAIGN = "campaign"


class Madhab(StrEnum):
    """
    Islamic jurisprudence school — affects only Asr prayer calculation.
    HANAFI uses shadow ratio 2 (later Asr); all others use ratio 1 (earlier Asr).
    Default for Bangladesh: HANAFI.
    """

    HANAFI = "hanafi"
    SHAFI = "shafi"
    MALIKI = "maliki"
    HANBALI = "hanbali"


class CalculationMethod(StrEnum):
    """
    Prayer time calculation method (Fajr/Isha twilight angles).
    Default for Bangladesh: KARACHI (University of Islamic Sciences, Fajr 18°, Isha 18°).
    """

    KARACHI = "karachi"
    MUSLIM_WORLD_LEAGUE = "muslim_world_league"
    ISNA = "isna"
    EGYPT = "egypt"
    MAKKAH = "makkah"
