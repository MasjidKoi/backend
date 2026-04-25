from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Import all models below so Alembic autogenerate detects them via Base.metadata.
# Must come AFTER Base is defined to avoid circular imports.
from app.models import announcement as _announcement  # noqa: F401, E402
from app.models import audit_log as _audit_log  # noqa: F401, E402
from app.models import masjid as _masjid  # noqa: F401, E402
from app.models import masjid_campaign as _masjid_campaign  # noqa: F401, E402
from app.models import (  # noqa: E402
    masjid_co_admin_invite as _masjid_co_admin_invite,  # noqa: F401
)
from app.models import masjid_event as _masjid_event  # noqa: F401, E402
from app.models import masjid_report as _masjid_report  # noqa: F401, E402
from app.models import masjid_review as _masjid_review  # noqa: F401, E402
from app.models import prayer_times as _prayer_times  # noqa: F401, E402
from app.models import support_ticket as _support_ticket  # noqa: F401, E402
from app.models import user_badge as _user_badge  # noqa: F401, E402
from app.models import user_checkin as _user_checkin  # noqa: F401, E402
from app.models import user_journal_entry as _user_journal_entry  # noqa: F401, E402
from app.models import user_masjid_follow as _user_masjid_follow  # noqa: F401, E402
from app.models import platform_settings as _platform_settings  # noqa: F401, E402
from app.models import user_profile as _user_profile  # noqa: F401, E402
