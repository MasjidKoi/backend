from sqlalchemy.orm import declarative_base

Base = declarative_base()

# Import all models below so Alembic autogenerate detects them via Base.metadata.
# Must come AFTER Base is defined to avoid circular imports.
from app.models import masjid as _masjid  # noqa: F401, E402
from app.models import prayer_times as _prayer_times  # noqa: F401, E402
